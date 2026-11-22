"""Plan applying Yamaha catalog metadata without changing any source files.

The caller supplies only MIDI candidates in the applicable folder or image.
Catalog filenames and known source identities take precedence over the numbered
names produced by the application's bulk extractor. Table order is never used.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .catalog_inspection import CatalogInspection
from .dos83_renamer import is_dos83_filename
from .smart_pianosoft import SmartPianoSoftSong


@dataclass(frozen=True)
class CatalogCandidate:
    """One loaded MIDI file, identified by a caller-owned, hashable key.

    ``filename`` and ``aliases`` may be basenames or paths. Supply source and
    staged names as aliases when appropriate. ``catalog_filename`` is an
    optional, authoritative identity retained from an earlier catalog import.
    """

    key: object
    filename: str
    aliases: tuple[str, ...] = ()
    catalog_filename: str = ""


@dataclass(frozen=True)
class CatalogTitleUpdate:
    key: object
    title: str
    catalog_filename: str
    track_number: int
    matched_by: str


@dataclass(frozen=True)
class CatalogApplicationPlan:
    album_title: str | None = None
    updates: tuple[CatalogTitleUpdate, ...] = ()
    missing_songs: tuple[SmartPianoSoftSong, ...] = ()
    ambiguous_songs: tuple[SmartPianoSoftSong, ...] = ()
    invalid_songs: tuple[SmartPianoSoftSong, ...] = ()
    empty_title_songs: tuple[SmartPianoSoftSong, ...] = ()
    unmatched_candidates: tuple[object, ...] = ()


_EXTRACTED_NAME = re.compile(r"^(\d{2,3})\s+-\s+.+\.mid(?:i)?$", re.IGNORECASE)
_RESERVED_STEM = re.compile(r"(?:CON|PRN|AUX|NUL|(?:COM|LPT)[1-9])", re.IGNORECASE)


def _basename(value):
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1]


def _safe_catalog_filename(value):
    """Catalog record names are basenames, never caller-supplied host paths."""
    return (
        isinstance(value, str)
        and not any(character in value for character in "/\\:")
        and is_dos83_filename(value)
        and value.casefold().endswith(".mid")
        and not _RESERVED_STEM.fullmatch(value.rsplit(".", 1)[0])
    )


def plan_catalog_application(inspected: CatalogInspection, candidates=()):
    """Return unambiguous title updates and the records that could not apply.

    Empty titles are preserved for inspection but do not erase existing titles.
    A known catalog identity is authoritative even if a staged filename happens
    to name another record. Without that identity, conflicting names, duplicate
    catalog entries, or multiple candidates for a song are left untouched.
    """
    if inspected.kind == "PDISK.MNG":
        return CatalogApplicationPlan(album_title=inspected.title or None)
    if inspected.kind != "PSONG.MNG":
        raise ValueError("Only PDISK.MNG and PSONG.MNG catalogs can be applied.")

    candidates = tuple(candidates)
    songs = inspected.songs
    valid_indices = [
        index for index, song in enumerate(songs)
        if _safe_catalog_filename(song.filename)
    ]
    by_name, by_track = defaultdict(set), defaultdict(set)
    for index in valid_indices:
        song = songs[index]
        by_name[song.filename.casefold()].add(index)
        by_track[song.track_number].add(index)

    edges, methods = [], []
    candidate_key_counts = Counter(candidate.key for candidate in candidates)
    for candidate in candidates:
        if candidate.catalog_filename:
            identities = (by_name.get(candidate.catalog_filename.casefold(), set())
                          if _safe_catalog_filename(candidate.catalog_filename) else set())
            method = "catalog_identity"
        else:
            names = {_basename(value) for value in (candidate.filename, *candidate.aliases)}
            identities = set().union(*(by_name.get(name.casefold(), set()) for name in names))
            method = "filename"
            if not identities:
                method = "extracted_track"
                for name in names:
                    match = _EXTRACTED_NAME.fullmatch(name)
                    if match:
                        identities.update(by_track.get(int(match.group(1)), set()))
        edges.append(identities)
        methods.append(method)

    candidates_by_song = defaultdict(list)
    ambiguous_indices = set()
    for identities in by_name.values():
        if len(identities) > 1:
            ambiguous_indices.update(identities)
    for candidate_index, identities in enumerate(edges):
        for song_index in identities:
            candidates_by_song[song_index].append(candidate_index)
        if len(identities) > 1 or candidate_key_counts[candidates[candidate_index].key] > 1:
            ambiguous_indices.update(identities)
    for song_index, matches in candidates_by_song.items():
        if len(matches) > 1:
            ambiguous_indices.add(song_index)

    updates, missing, ambiguous, invalid, empty = [], [], [], [], []
    applied_candidate_indices = set()
    valid_indices = set(valid_indices)
    for index, song in enumerate(songs):
        if index not in valid_indices:
            invalid.append(song)
        elif index in ambiguous_indices:
            ambiguous.append(song)
        elif not song.title:
            empty.append(song)
        elif not candidates_by_song[index]:
            missing.append(song)
        else:
            candidate_index = candidates_by_song[index][0]
            candidate = candidates[candidate_index]
            updates.append(CatalogTitleUpdate(
                candidate.key, song.title, song.filename, song.track_number,
                methods[candidate_index],
            ))
            applied_candidate_indices.add(candidate_index)

    return CatalogApplicationPlan(
        updates=tuple(updates), missing_songs=tuple(missing),
        ambiguous_songs=tuple(ambiguous), invalid_songs=tuple(invalid),
        empty_title_songs=tuple(empty),
        unmatched_candidates=tuple(candidate.key for index, candidate in enumerate(candidates)
                                   if index not in applied_candidate_indices),
    )
