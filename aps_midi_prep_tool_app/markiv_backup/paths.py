# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Link checks shared by the read-only catalog and copy engine."""

from pathlib import Path
import stat


def is_link(path: Path) -> bool:
    """Include Windows junctions, also on Python before Path.is_junction()."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0) == 0xA0000003
