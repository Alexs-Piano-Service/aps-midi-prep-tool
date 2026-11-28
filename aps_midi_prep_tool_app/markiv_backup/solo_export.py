# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Publish verified piano-only MIDI alongside prepared accompaniment songs."""

import io
from pathlib import Path, PurePosixPath
import shutil

import mido

from .library import CancelledError, check_cancel


def _contains_performance(payload, cancel):
    midi = mido.MidiFile(file=io.BytesIO(payload))
    if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
        raise ValueError('Standalone playback requires type 0 or 1 MIDI with beat-based timing.')
    for track in midi.tracks:
        for message in track:
            check_cancel(cancel)
            if message.type == 'note_on' and message.velocity > 0:
                return True
    return False


def prepare_solo_midi_copies(plan, folder, original_root, records, manifest, journal,
                             *, cancel=None, progress=None, generate_yamaha_catalogs=False,
                             cataloged_sources=(), resolution=None):
    """Copy playable MIDI whose catalog does not require separate accompaniment.

    The source bytes are checked and parsed as one immutable in-memory snapshot.
    MIDI with an unresolved companion is left to the pairing report rather than
    silently delivered as a complete piano-only song.
    """
    from .backup import (
        _audio_pair_root, _audio_pair_stem, _contained, _manifest_allowance,
        _read_conversion_source, _write_derivative,
    )
    from .playback import resolve_playback_pairs

    if resolution is None:
        resolution = resolve_playback_pairs(
            plan, cancel=cancel, progress=progress, work_dir=folder,
            source_paths={item.source: _contained(original_root, item.destination) for item in plan.files},
        )
    paired = {pair.midi.source for pair in resolution.pairs}
    unresolved = {issue.midi_source for issue in resolution.issues}
    candidates = [
        (index, item, record)
        for index, (item, record) in enumerate(zip(plan.files, records))
        if item.kind not in {'Metadata', 'PianoSoft package'}
        and (item.kind == 'MIDI' or Path(item.source).suffix.lower() in {'.mid', '.midi'})
        and PurePosixPath(item.original_source or item.source).parts[:2] != ('songs', 'pspg')
        and item.source not in paired
        and (item.original_source or item.source) not in unresolved
        and record['status'] in {'verified', 'referenced'}
    ]
    root = _audio_pair_root(folder, plan, 'MIDI')
    reserved = set()
    prepared = failed = 0

    def emit(item, completed):
        if progress:
            progress({'event': 'audio_prepare', 'message': f'Preparing {item.destination}',
                      'completed': completed, 'total': len(candidates),
                      'audio_total': len(candidates), 'decoded': prepared})

    for completed, (index, item, record) in enumerate(candidates):
        check_cancel(cancel)
        details = record['playback_prepare'] = {
            'status': 'pending', 'format': 'midi', 'association': 'solo_midi',
        }
        derivative = None
        derivative_index = None
        journal.append('files', index, record)
        emit(item, completed)
        try:
            if shutil.disk_usage(folder).free < max(item.size, 64 * 1024) + _manifest_allowance(plan):
                raise ValueError('Not enough free space for standalone MIDI preparation and the manifest.')
            original = _contained(original_root, item.destination)
            payload = _read_conversion_source(original, record['sha256'], cancel)
            if not _contains_performance(payload, cancel):
                # Catalog wrappers and setup-only files are preserved in the
                # archive, without counting them as skipped accompaniment.
                details.update(status='not_applicable', reason='The MIDI contains no played notes.')
                continue
            stem = _audio_pair_stem(
                folder, root, item.destination, reserved,
                short_names=(generate_yamaha_catalogs or
                             Path(item.source).parent.as_posix() in cataloged_sources),
                extensions=('.mid',),
            )
            relative = Path(str(stem) + '.mid')
            destination = _contained(folder, relative.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            derivative = {
                'source': item.source, 'original': item.destination,
                'destination': relative.as_posix(), 'kind': 'MIDI',
                'conversion': 'Verified standalone MIDI playback copy', 'status': 'pending',
            }
            derivative_index = len(manifest['derivatives'])
            manifest['derivatives'].append(derivative)
            journal.append('derivatives', derivative_index, derivative)
            checksum = _write_derivative(destination, payload, cancel)
            derivative.update(status='verified', size=len(payload), sha256=checksum)
            journal.append('derivatives', derivative_index, derivative)
            details.update(status='prepared', destinations=[relative.as_posix()], source_preserved=True)
            prepared += 1
        except Exception as exc:
            interrupted = isinstance(exc, (CancelledError, InterruptedError))
            details.update(status='cancelled' if interrupted else 'failed', error=str(exc))
            if derivative is not None and derivative['status'] != 'verified':
                derivative.update(status=details['status'], error=str(exc))
                journal.append('derivatives', derivative_index, derivative)
            if interrupted:
                raise CancelledError('Cancelled by user') from None
            message = f'{item.source}: Standalone MIDI preparation failed: {exc}'
            manifest['errors'].append(message)
            failed += 1
            if progress:
                progress({'event': 'error', 'message': message})
        finally:
            journal.append('files', index, record)
            emit(item, completed + 1)
    return prepared, failed
