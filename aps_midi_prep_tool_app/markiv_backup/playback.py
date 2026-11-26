# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Resolve Mark IV music associations and validate their playback timelines.

Album titles and MIDI display order are not evidence of a CD pairing. A
separate CD recording needs the piano's recorded association, or the MIDI's
actual CD-track reference and an exact catalog CD ID, followed by fingerprint
validation. Stored ``sps_offset`` values are retained as provenance; their
units and applicability are not assumed.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import io
import math
from pathlib import Path, PurePosixPath

import mido

from .library import BackupPlan, PlannedFile, check_cancel
from ..disklavier_audio import is_pianosmart_message
from ..smart_pianosoft_sync import parse_sps_midi, synchronize


@dataclass(frozen=True)
class PlaybackPair:
    midi: PlannedFile
    audio: PlannedFile
    association: str
    cd_track: int | None = None
    sync_links: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PlaybackIssue:
    midi_source: str
    audio_source: str
    message: str
    code: str


@dataclass(frozen=True)
class PlaybackPairPlan:
    pairs: tuple[PlaybackPair, ...]
    issues: tuple[PlaybackIssue, ...]
    paired_audio_sources: frozenset[str]


@dataclass(frozen=True)
class PreparedPair:
    events: tuple[tuple[float, bytes], ...]
    audio_path: Path
    offset_seconds: float
    time_scale: float
    alignment_details: dict


def _original(item):
    return getattr(item, 'original_source', '') or item.source


def _source_path(value):
    """Normalize only local songs-tree references, never removable-media paths."""
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value.replace('\\', '/'))
    if '..' in path.parts:
        return None
    if path.parts[:2] == ('/', 'home'):
        path = PurePosixPath(*path.parts[2:])
    if path.is_absolute() or not path.parts or path.parts[0] != 'songs':
        return None
    return path.as_posix()


def _midi_payload(path):
    data = Path(path).read_bytes()
    if not data.startswith(b'MThd'):
        from ..eseq_converter import convert_eseq_bytes_to_midi_bytes
        data = convert_eseq_bytes_to_midi_bytes(data, filename=Path(path).name)
    return data


def _read_midi(path, cancel=None):
    data = _midi_payload(path)
    midi = mido.MidiFile(file=io.BytesIO(data))
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise ValueError('Paired playback requires a type 0 or 1 MIDI with beat-based timing.')
    elapsed, events, has_sync = 0.0, [], False
    for message in midi:
        check_cancel(cancel)
        elapsed += message.time
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('MIDI event timing is invalid.')
        if message.is_meta:
            if message.type == 'sequencer_specific' and tuple(message.data[:3]) == (0x43, 0x71, 0x7D):
                has_sync = True
        else:
            wire = bytes(message.bytes())
            if is_pianosmart_message(wire) and wire[3] == 0x7B:
                has_sync = True
            events.append((elapsed, wire))
    if not events:
        raise ValueError('The MIDI contains no musical events.')
    return data, tuple(events), has_sync


def _inspect_sync(path, cancel):
    """Inspect headers without merging every track during association planning."""
    data = _midi_payload(path)
    midi = mido.MidiFile(file=io.BytesIO(data))
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise ValueError('Paired playback requires a type 0 or 1 MIDI with beat-based timing.')
    for track in midi.tracks:
        for message in track:
            check_cancel(cancel)
            if message.type == 'sequencer_specific' and tuple(message.data[:3]) == (0x43, 0x71, 0x7D):
                return data, True
            # A CD-start command (43 71 05) also appears in native PlusAudio
            # MIDI/WAV pairs. It is not a fingerprint or a timing reference.
            if message.type == 'sysex' and tuple(message.data[:3]) == (0x43, 0x71, 0x7B):
                return data, True
    return data, False


def resolve_playback_pairs(plan: BackupPlan, cancel=None, *, source_paths=None) -> PlaybackPairPlan:
    """Resolve candidates without altering files or asserting they are synchronized.

    ``paired_audio_sources`` identifies plan source paths claimed by unique
    associations. Each resulting pair still needs :func:`prepare_pair` before
    export. Multiple distinct MIDI performances may legitimately use one CD
    recording. Ambiguous associations and known missing companions are explicit
    issues instead of guessed matches. ``source_paths`` may map plan source
    identities to verified archived copies. When supplied it is authoritative:
    an absent entry is not read from the original drive as a fallback.
    """
    files = [item for item in plan.files
             if item.kind not in {'Metadata', 'PianoSoft package'}
             and PurePosixPath(_original(item)).name.casefold() != 'pianodir.fil'
             and PurePosixPath(_original(item)).parts[:2] != ('songs', 'pspg')]
    by_source = defaultdict(list)
    audio_by_stem, audio_by_cd_track, midi_by_stem = defaultdict(list), defaultdict(list), defaultdict(list)
    audio_by_cd_stem = defaultdict(list)
    audio_sources, midi_items = set(), []
    for item in files:
        check_cancel(cancel)
        source = _original(item)
        by_source[source].append(item)
        suffix = PurePosixPath(source).suffix.lower()
        if suffix in {'.wav', '.mp3'}:
            audio_sources.add(item.source)
            path = PurePosixPath(source)
            audio_by_stem[(str(path.parent), path.stem.casefold())].append(item)
            cd_id, track = item.metadata.get('cd_id'), item.metadata.get('track_number')
            if cd_id:
                audio_by_cd_stem[(cd_id, path.stem.casefold())].append(item)
            if cd_id and str(track).isdigit() and int(track) > 0:
                audio_by_cd_track[(cd_id, int(track))].append(item)
        elif item.kind in {'MIDI', 'E-SEQ'} or suffix in {'.mid', '.midi'}:
            midi_items.append(item)
            path = PurePosixPath(source)
            midi_by_stem[(str(path.parent), path.stem.casefold())].append(item)

    links_by_midi = defaultdict(list)
    issues, seen_issues, pairs = [], set(), []

    def issue(midi, audio, message, code):
        key = (midi, audio, code)
        if key not in seen_issues:
            seen_issues.add(key)
            issues.append(PlaybackIssue(midi, audio, message, code))

    for link in getattr(plan, 'sync_links', ()):
        check_cancel(cancel)
        midi_source = _source_path(link.get('midi_path'))
        audio_source = _source_path(link.get('wave_path'))
        if midi_source:
            links_by_midi[midi_source].append(link)
        historical_audio = None
        if audio_source in by_source:
            matches = [a for a in by_source[audio_source] if a.source in audio_sources]
            if len(matches) == 1:
                historical_audio = matches[0]
        elif isinstance(link.get('wave_path'), str) and link.get('cdtoc'):
            # Older rows may retain only "track-01" or a former album path.
            # An exact CD identity plus the original filename stem can explain
            # a missing performance. This is evidence for a warning, not a
            # substitute pairing or a timing assertion.
            recorded = PurePosixPath(link['wave_path'].replace('\\', '/'))
            if '..' not in recorded.parts:
                matches = audio_by_cd_stem.get((link['cdtoc'], recorded.stem.casefold()), ())
                if len(matches) == 1:
                    historical_audio = matches[0]
        if historical_audio is not None and midi_source not in by_source:
            missing = midi_source or str(link.get('midi_path') or 'unrecorded MIDI source')
            issue(missing, _original(historical_audio),
                  'The catalog references a MIDI performance absent from this backup; '
                  'the audio was not paired with a substitute.', 'missing_midi')

    for item in midi_items:
        check_cancel(cancel)
        source = _original(item)
        path = PurePosixPath(source)
        same_stem = list(audio_by_stem.get((str(path.parent), path.stem.casefold()), ()))
        # Prefer an original PCM companion to a compressed copy, as playback does.
        same_stem = [a for a in same_stem if PurePosixPath(_original(a)).suffix.lower() == '.wav'] or same_stem
        add_file = item.metadata.get('add_file')
        explicit_companion = []
        required_audio_source = None
        if isinstance(add_file, str) and add_file:
            relative = PurePosixPath(add_file.replace('\\', '/'))
            if (not relative.is_absolute() and '..' not in relative.parts
                    and relative.suffix.lower() in {'.wav', '.mp3'}):
                required_audio_source = str(path.parent / relative)
                explicit_companion = [a for a in by_source.get(required_audio_source, ())
                                      if a.source in audio_sources]
        native = {a.source: a for a in same_stem + explicit_companion}
        cd_id = item.metadata.get('cd_id')
        links = [link for link in links_by_midi.get(source, ())
                 if not cd_id or not link.get('cdtoc') or link.get('cdtoc') == cd_id]
        declared_sps = str(item.metadata.get('type', '')).upper() == 'SPS'
        if not native and not links and not cd_id and not declared_sps and required_audio_source is None:
            continue  # An ordinary piano-only performance needs no audio pair.
        if required_audio_source is not None and not explicit_companion:
            issue(source, required_audio_source,
                  'The catalog requires an audio companion absent from this backup; '
                  'the MIDI was not treated as a piano-only performance.', 'missing_audio')
            continue
        try:
            midi_path = plan.source / item.source if source_paths is None else source_paths[item.source]
            data, has_sync = _inspect_sync(midi_path, cancel)
            if str(item.metadata.get('type', '')).upper() == 'AUDIO' and not any(
                    not message.is_meta for track in mido.MidiFile(file=io.BytesIO(data)).tracks
                    for message in track):
                continue  # Audio-only catalog entries can have metadata-only SMF wrappers.
            metadata = parse_sps_midi(data) if has_sync or links or declared_sps else None
        except (OSError, ValueError, EOFError, KeyError, IndexError, TypeError) as exc:
            issue(source, '', f'Could not validate the MIDI playback metadata: {exc}', 'invalid_midi')
            continue

        direct = {}
        missing_audio = []
        for link in links:
            audio_source = _source_path(link.get('wave_path'))
            if not audio_source:
                continue
            matches = [a for a in by_source.get(audio_source, ()) if a.source in audio_sources]
            if not matches:
                missing_audio.append(audio_source)
            for audio in matches:
                if cd_id and audio.metadata.get('cd_id') not in (None, '', cd_id):
                    issue(source, audio_source, 'The recorded audio association has a different CD ID.',
                          'conflicting_cd_id')
                    continue
                direct[audio.source] = audio

        cd_track = metadata.cd_track if metadata else None
        # Track number comes from the MIDI's CD TOC, never its display order.
        recorded_cd = cd_id or (links[0].get('cdtoc') if links and
                                len({link.get('cdtoc') for link in links}) == 1 else None)
        indexed = {a.source: a for a in audio_by_cd_track.get((recorded_cd, cd_track), ())}
        candidates = {**native, **direct, **indexed}
        if len(candidates) != 1 or (len(by_source[source]) != 1):
            if candidates:
                issue(source, '', 'More than one audio companion is supported by the catalog; '
                      'no pairing was chosen.', 'ambiguous_audio')
            elif metadata or declared_sps or links or required_audio_source is not None:
                issue(source, missing_audio[0] if missing_audio else '',
                      'No unique audio companion is available for this MIDI performance. '
                      'Album order and song titles were not used as substitutes.', 'missing_audio')
            continue
        audio = next(iter(candidates.values()))
        if direct and indexed and set(direct) != set(indexed):
            issue(source, _original(audio), 'The stored audio path and recorded CD track disagree.',
                  'conflicting_audio')
            continue
        if not metadata and same_stem and len(midi_by_stem[(str(path.parent), path.stem.casefold())]) > 1:
            issue(source, _original(audio), 'More than one MIDI matches this audio companion; '
                  'no pairing was chosen.', 'ambiguous_midi')
            continue
        association = 'sps_metadata' if metadata else 'same_stem' if same_stem else 'catalog_companion'
        pairs.append(PlaybackPair(item, audio, association, cd_track, tuple(dict(row) for row in links)))

    return PlaybackPairPlan(tuple(pairs), tuple(issues), frozenset(pair.audio.source for pair in pairs))


def prepare_pair(pair: PlaybackPair, midi_path, audio_path, *, cancel=None) -> PreparedPair:
    """Validate a pair and return the shared MIDI-to-audio timing map.

    Paths are verified snapshots supplied by the exporter. Audio must already
    be canonical PCM WAV and checked for embedded control data. Existing native
    companions retain their timeline; Smart PianoSoft companions must pass both
    audio fingerprints even when a saved offset is present in the catalog.
    """
    check_cancel(cancel)
    data, events, has_sync = _read_midi(midi_path, cancel)
    details = {'association': pair.association, 'cd_track': pair.cd_track,
               'stored_sync_links': [dict(row) for row in pair.sync_links]}
    if pair.association == 'sps_metadata' or has_sync:
        metadata = parse_sps_midi(data)
        alignment = synchronize(metadata, audio_path, cancel=cancel)
        events = tuple((event.time_seconds, event.data) for event in metadata.events)
        details.update(method='two_audio_fingerprints', **asdict(alignment))
        offset, scale = alignment.offset_seconds, alignment.time_scale
    else:
        offset, scale = 0.0, 1.0
        details.update(method='existing_companion_timeline', offset_seconds=offset, time_scale=scale)
    check_cancel(cancel)
    return PreparedPair(events, Path(audio_path), offset, scale, details)
