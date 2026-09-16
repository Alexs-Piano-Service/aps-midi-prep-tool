# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Build a deterministic, non-destructive export plan for a Mark IV data volume."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import unicodedata

from ..helpers.portable_filename import is_windows_device_name
from ..eseq_converter import is_eseq_file
from .paths import is_link

MUSIC_EXTENSIONS = {'.mid', '.midi', '.fil', '.mda', '.eseq', '.esq', '.e-seq', '.seq', '.wav', '.mp3', '.pspg'}
CATEGORIES = {'user': 'User', 'pianosoft': 'PianoSoft', 'audio': 'Audio',
              'demo': 'Demo', 'diag': 'Diagnostics', 'pspg': 'PianoSoft presentations'}


class CancelledError(Exception):
    """The user stopped scanning or copying."""


@dataclass
class PlannedFile:
    source: str
    destination: str
    size: int
    kind: str
    metadata: dict = field(default_factory=dict)
    mtime_ns: int = 0
    convertible_eseq: bool = False


@dataclass
class Album:
    name: str
    category: str
    file_count: int
    total_bytes: int
    destination: str = ''
    source: str = ''
    metadata: dict = field(default_factory=dict)


@dataclass
class BackupPlan:
    source: Path
    files: list[PlannedFile]
    albums: list[Album]
    warnings: list[str]
    total_bytes: int
    scan_errors: list[str] = field(default_factory=list)


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError('Cancelled by user')


def safe_name(value: str, fallback: str = 'Untitled', limit: int = 150) -> str:
    """Portable filename component, limited in bytes for Linux and removable disks."""
    value = unicodedata.normalize('NFC', str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', ' - ', value)
    value = re.sub(r'\s+', ' ', value).strip(' .') or fallback
    if is_windows_device_name(value):
        value = '_' + value
    value = value.encode('utf-8')[:limit].decode('utf-8', 'ignore').rstrip(' .')
    return value or fallback


def _unique(path: Path, used: set[str]) -> Path:
    result = path
    number = 2
    while unicodedata.normalize('NFC', result.as_posix()).casefold() in used:
        result = path.with_name(f'{path.stem} ({number}){path.suffix}')
        number += 1
    used.add(unicodedata.normalize('NFC', result.as_posix()).casefold())
    return result


def _walk(root: Path, warnings: list[str], cancel):
    def relevant(path):
        rel = Path(path).relative_to(root)
        # System symlinks and inaccessible program directories aren't music.
        return (rel.parts[0] not in {'local', 'tgm4', 'firm', 'postgres', 'lost+found'}
                or rel.suffix.lower() in MUSIC_EXTENSIONS
                or rel.parts[:3] == ('local', 'pgsql', 'data'))
    def on_error(error):
        if relevant(error.filename):
            warnings.append(f'Could not scan {error.filename}: {error.strerror}')
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=on_error):
        check_cancel(cancel)
        dirs.sort()
        for name in dirs[:]:
            if is_link(Path(folder) / name):
                if relevant(Path(folder, name)):
                    warnings.append(f'Skipped symbolic link: {Path(folder, name).relative_to(root)}')
                dirs.remove(name)
        for name in sorted(files):
            p = Path(folder) / name
            try:
                st = p.lstat()
                if not stat.S_ISREG(st.st_mode):
                    if relevant(p) and (p.suffix.lower() in MUSIC_EXTENSIONS or p.relative_to(root).parts[0] == 'songs'):
                        warnings.append(f'Skipped non-regular file: {p.relative_to(root)}')
                    continue
                yield p, st
            except OSError as exc:
                warnings.append(f'Could not inspect {p.relative_to(root)}: {exc}')


def _kind(path: Path) -> str:
    return {'.mid': 'MIDI', '.midi': 'MIDI', '.fil': 'E-SEQ', '.mda': 'E-SEQ', '.eseq': 'E-SEQ',
            '.esq': 'E-SEQ', '.e-seq': 'E-SEQ', '.seq': 'E-SEQ', '.wav': 'WAV', '.mp3': 'MP3', '.pspg': 'PianoSoft package'}.get(
                path.suffix.lower(), 'Album asset')


def is_conversion_candidate(path: Path, kind: str) -> bool:
    """Share content-based eligibility between scan previews and conversion."""
    return (kind not in {'Metadata', 'PianoSoft package'}
            and path.suffix.lower() != '.pspg'
            and not is_link(path)
            and is_eseq_file(str(path)))


def scan_library(source: Path, progress=None, cancel=None) -> BackupPlan:
    """Read every songs/ file, known music elsewhere, and original metadata.

    No files are executed, changed, converted, or deduplicated. Unrecognized files
    in the music library are retained because they can be playback dependencies.
    """
    from .metadata import load_metadata
    from .tags import read_folder_tags, read_music_title

    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir() or not (source / 'songs').is_dir():
        raise ValueError('Select the Mark IV data volume containing the songs folder.')
    if is_link(source / 'songs'):
        raise ValueError('The songs folder must be a real directory, not a symbolic link.')
    check_cancel(cancel)
    if progress:
        progress({'event': 'scan', 'message': 'Reading the music catalog…'})
    catalog = load_metadata(source)
    warnings = list(catalog.warnings)
    check_cancel(cancel)
    evidence = set(catalog.evidence_files)
    scan_errors = []
    inventory = list(_walk(source, scan_errors, cancel))
    warnings.extend(scan_errors)
    selected = []
    for path, st in inventory:
        rel = path.relative_to(source)
        # Catalog schemas are evidence rather than a music album.
        metadata = rel.as_posix() in evidence or rel.parts[:2] == ('songs', 'tool')
        if metadata or rel.parts[0] == 'songs' or path.suffix.lower() in MUSIC_EXTENSIONS:
            selected.append((rel, st, metadata))
    if not any(not metadata for _, _, metadata in selected):
        raise ValueError('No music or album assets were found in this volume.')

    groups = defaultdict(list)
    metadata_files = []
    for rel, st, is_metadata in selected:
        if is_metadata:
            metadata_files.append((rel, st))
            continue
        parts = rel.parts
        if parts[:4] == ('songs', 'pspg', 'archive', 'cache') and len(parts) > 5:
            album_dir = Path(*parts[:5])
            category = 'PianoSoft presentations'
        elif parts[0] == 'songs' and len(parts) >= 4:
            album_dir = Path(*parts[:3])
            category = CATEGORIES.get(parts[1], safe_name(parts[1].title()))
        elif parts[0] == 'songs':
            album_dir = rel.parent
            category = 'Other music'
        else:
            album_dir = rel.parent
            category = 'Imports'
        groups[(album_dir, category)].append((rel, st))

    files = []
    albums = []
    used = set()
    used_folders = set()
    presentations = {f"pianosoft{tags.get('song_id')}": tags for path, tags in catalog.tracks.items()
                     if path.startswith('songs/pianosoft/') and path.lower().endswith('.pspg')}
    for (album_dir, category), members in sorted(groups.items(), key=lambda x: str(x[0])):
        check_cancel(cancel)
        try:
            side_album, side_tracks = read_folder_tags(source / album_dir)
        except (OSError, ValueError) as exc:
            side_album, side_tracks = {}, {}
            warnings.append(f'Could not read album index {album_dir}: {exc}')
        db_album = catalog.albums.get(album_dir.as_posix(), {})
        album_name = db_album.get('title') or side_album.get('title')
        # A presentation cache refers to the same PianoSoft package in the catalog.
        if not album_name and category == 'PianoSoft presentations':
            album_name = presentations.get(album_dir.name, {}).get('title')
        album_name = album_name or (f'{category} {album_dir.name}' if album_dir.name.isdigit() else album_dir.name)
        dest_folder = _unique(Path(category) / safe_name(album_name), used_folders)
        # Share title/order between audio and MIDI companions with the same stem.
        track_tags = {}
        folder_tags = {album_dir: side_tracks}
        for rel, _ in members:
            if rel.parent not in folder_tags:
                try:
                    _, folder_tags[rel.parent] = read_folder_tags(source / rel.parent)
                except (OSError, ValueError) as exc:
                    folder_tags[rel.parent] = {}
                    warnings.append(f'Could not read song index {rel.parent}: {exc}')
            tags = dict(folder_tags[rel.parent].get(rel.name, {}))
            if not tags.get('title') and rel.suffix.lower() in {'.mid', '.midi'}:
                try:
                    title = read_music_title(source / rel)
                    if title:
                        tags.update(title=title, metadata_source='MIDI title')
                except (OSError, ValueError) as exc:
                    warnings.append(f'Could not read song title {rel}: {exc}')
            if tags:
                track_tags.setdefault((rel.parent, rel.stem.casefold()), {}).update(tags)
        # Database labels outrank every sidecar, regardless of filename sorting.
        for rel, _ in members:
            tags = {k: v for k, v in catalog.tracks.get(rel.as_posix(), {}).items() if v is not None and v != ''}
            if tags:
                track_tags.setdefault((rel.parent, rel.stem.casefold()), {}).update(tags)
        named_groups = {}
        reserved_names = set()
        companion_suffixes = defaultdict(set)
        for rel, _ in members:
            if rel.suffix.lower() in MUSIC_EXTENSIONS:
                companion_suffixes[(rel.parent, rel.stem.casefold())].add(rel.suffix)
        for rel, st in members:
            check_cancel(cancel)
            tags = dict(track_tags.get((rel.parent, rel.stem.casefold()), {}))
            tags.update({k: v for k, v in catalog.tracks.get(rel.as_posix(), {}).items() if v is not None and v != ''})
            title = tags.get('title')
            if not title and rel.suffix.lower() in {'.mid', '.midi'}:
                try:
                    title = read_music_title(source / rel)
                except (OSError, ValueError) as exc:
                    warnings.append(f'Could not read song title {rel}: {exc}')
                if title:
                    tags.update(title=title, metadata_source='MIDI title')
            if not title:
                title = rel.stem
                tags.setdefault('metadata_source', 'Original filename')
            nested = rel.relative_to(album_dir)
            # Presentation scripts refer to exact asset names; retain their layout.
            if category == 'PianoSoft presentations' or rel.suffix.lower() not in MUSIC_EXTENSIONS:
                destination = dest_folder.joinpath(*(safe_name(p) for p in nested.parts))
            else:
                number = tags.get('track_number')
                prefix = f'{int(number):02d} - ' if str(number).isdigit() and int(number) > 0 else ''
                stem = safe_name(prefix + str(title))
                parent = dest_folder.joinpath(*(safe_name(p) for p in nested.parts[:-1]))
                group = (rel.parent, rel.stem.casefold(), stem)
                if group not in named_groups:
                    candidate = stem
                    counter = 2
                    suffixes = companion_suffixes[(rel.parent, rel.stem.casefold())]
                    while any((parent / (candidate + suffix)).as_posix().casefold() in used | reserved_names for suffix in suffixes):
                        candidate = f'{stem} ({counter})'
                        counter += 1
                    named_groups[group] = candidate
                    reserved_names.update((parent / (candidate + suffix)).as_posix().casefold() for suffix in suffixes)
                destination = parent / (named_groups[group] + rel.suffix)
            destination = _unique(destination, used)
            tags.update(album=str(album_name), category=category, title=str(title),
                        original_filename=rel.name)
            files.append(PlannedFile(rel.as_posix(), destination.as_posix(), st.st_size,
                                     _kind(rel), tags, st.st_mtime_ns,
                                     is_conversion_candidate(source / rel, _kind(rel))))
        albums.append(Album(str(album_name), category, len(members),
                            sum(st.st_size for _, st in members), dest_folder.as_posix(),
                            album_dir.as_posix(), {**side_album, **{
                                key: value for key, value in db_album.items() if value not in (None, '')
                            }}))
    for rel, st in sorted(metadata_files):
        dest = _unique(Path('_metadata/original') / rel, used)
        files.append(PlannedFile(rel.as_posix(), dest.as_posix(), st.st_size, 'Metadata', {}, st.st_mtime_ns))
    if progress:
        progress({'event': 'scan_complete', 'message': f'Found {len(albums)} albums and {len(files)} files.',
                  'completed': len(files), 'total': len(files)})
    return BackupPlan(source, files, albums, warnings, sum(f.size for f in files), scan_errors)
