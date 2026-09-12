"""Verify that every mtools command used by image regression tests can run."""

import shutil
import subprocess
import sys


MTOOLS_COMMANDS = ("mformat", "mcopy", "mdir", "mdel", "mren")


def main():
    failed = False
    for name in MTOOLS_COMMANDS:
        path = shutil.which(name)
        if path is None:
            print(f"{name}: not found on PATH", file=sys.stderr)
            failed = True
            continue
        print(f"{name}: {path}", flush=True)
        try:
            result = subprocess.run(
                [path, "-V"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"{name}: could not run version check: {exc}", file=sys.stderr)
            failed = True
            continue
        if result.stdout:
            print(result.stdout.rstrip(), flush=True)
        if result.returncode != 0:
            print(f"{name}: version check exited with code {result.returncode}", file=sys.stderr)
            failed = True
    if failed:
        print("Working mtools commands are required for image regression tests.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
