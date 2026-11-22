"""Name generated outputs without reusing existing files or directories."""

from itertools import count
import os
from pathlib import Path
import uuid


SETTING_RANDOM_OUTPUT_SUFFIXES = "random_output_suffixes"


def random_output_suffixes_enabled(settings):
    return settings.value(SETTING_RANDOM_OUTPUT_SUFFIXES, True, type=bool)


def output_path_candidates(parent, stem, *, suffix="", random_suffixes=True, separator="-"):
    """Yield names only; callers must create or publish each candidate exclusively."""
    parent = Path(parent)
    stem = str(stem)
    if not stem or stem in {".", ".."} or any(c in stem + suffix + separator for c in "/\\\0"):
        raise ValueError("An output name must be a single filename.")
    for number in count(1):
        if random_suffixes:
            ending = separator + uuid.uuid4().hex[:8]
        else:
            ending = "" if number == 1 else f" ({number})"
        yield parent / f"{stem}{ending}{suffix}"


def create_output_directory(parent, stem, *, random_suffixes=True, separator="-"):
    """Atomically claim a new output folder, skipping files, folders and links."""
    for candidate in output_path_candidates(parent, stem, random_suffixes=random_suffixes,
                                            separator=separator):
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return candidate


def available_output_path(parent, stem, *, suffix="", random_suffixes=True, separator="-"):
    """Plan a name without writing; the eventual writer must still refuse replacement."""
    for candidate in output_path_candidates(parent, stem, suffix=suffix,
                                            random_suffixes=random_suffixes, separator=separator):
        if not os.path.lexists(candidate):
            return candidate
