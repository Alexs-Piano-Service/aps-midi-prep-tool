"""Publish playback copies from verified Mark IV originals and catalog links."""

from dataclasses import asdict
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .library import CancelledError, check_cancel


def _snapshot_source(source, destination, expected_size, expected_sha256, *, cancel=None):
    """Make a private, verified input before any parser or exporter reads it."""
    from .backup import _hash

    check_cancel(cancel)
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as incoming:
        before = os.fstat(incoming.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise ValueError('Original changed before playback preparation')
        count = 0
        with destination.open('xb') as outgoing:
            while block := incoming.read(1024 * 1024):
                check_cancel(cancel)
                outgoing.write(block)
                count += len(block)
        after = os.fstat(incoming.fileno())
        if (count != expected_size or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError('Original changed while creating playback input')
    if _hash(destination, cancel) != expected_sha256:
        raise ValueError('Original checksum changed before playback preparation')
    return destination


def prepare_playback_copies(plan, folder, original_root, records, manifest, journal,
                            audio_output, *, cancel=None, progress=None,
                            generate_yamaha_catalogs=False, cataloged_sources=(), resolution=None):
    from .backup import (
        _audio_candidates, _audio_pair_root, _audio_pair_stem, _audio_working_allowance,
        _contained, _hash, _manifest_allowance,
    )
    from .playback import prepare_pair, resolve_playback_pairs
    from ..audio_preparation import ENCODED_WAV
    from ..audio_tools import decode_mp3
    from ..disklavier_audio import DisklavierEncodingCancelled, encode_disklavier_wav
    from ..disklavier_audio_decode import decode_disklavier_audio, NotEncodedAudioError
    from ..helpers.atomic_file import publish_new_file
    from ..paired_audio import export_midi_mp3

    if resolution is None:
        resolution = resolve_playback_pairs(
            plan, cancel=cancel, progress=progress, work_dir=folder,
            source_paths={item.source: _contained(original_root, item.destination) for item in plan.files},
        )
    indexed = {item.source: (index, item, record)
               for index, (item, record) in enumerate(zip(plan.files, records))}
    originals = {item.original_source or item.source: item for item in plan.files}
    manifest['playback_issues'] = [asdict(issue) for issue in resolution.issues]
    failed = 0
    issue_sources = set()
    for issue in resolution.issues:
        item = originals.get(issue.midi_source)
        message = f'{issue.midi_source or issue.audio_source}: {issue.message}'
        if item is not None:
            issue_sources.add(item.source)
            index, _, record = indexed[item.source]
            record['playback_prepare'] = {'status': 'failed', 'error': issue.message}
            manifest['errors'].append(message)
            journal.append('files', index, record)
            failed += 1
        else:
            # Historical references can outlive the songs on the Mark IV.
            # Preserve that evidence without inventing a missing performance.
            manifest['warnings'].append(message)
    journal.snapshot()

    tasks = [(pair.midi, pair.audio, pair) for pair in resolution.pairs]
    tasks.extend((item, item, None) for item in _audio_candidates(plan)
                 if item.source not in resolution.paired_audio_sources)
    root = _audio_pair_root(folder, plan, 'Encoded WAV' if audio_output == ENCODED_WAV
                            else 'Paired MIDI and MP3')
    extensions = ('.wav',) if audio_output == ENCODED_WAV else ('.mid', '.mp3')
    prepared = skipped = 0
    reserved = set()

    def emit(item, completed):
        if progress:
            progress({'event': 'audio_prepare', 'message': f'Preparing {item.destination}',
                      'completed': completed, 'total': len(tasks), 'decoded': prepared,
                      'audio_total': len(tasks), 'audio_skipped': skipped})

    for completed, (item, audio_item, pair) in enumerate(tasks):
        check_cancel(cancel)
        index, _, record = indexed[item.source]
        audio_record = indexed[audio_item.source][2]
        if item.source in issue_sources:
            continue
        details = record['playback_prepare'] = {
            'status': 'pending', 'format': audio_output,
            'audio_source': audio_item.original_source or audio_item.source,
            'association': pair.association if pair else 'embedded_control',
        }
        derivative_indices = []
        journal.append('files', index, record)
        emit(item, completed)
        try:
            sources = {item.source: (item, record), audio_item.source: (audio_item, audio_record)}
            for source_item, source_record in sources.values():
                if source_record['status'] not in {'verified', 'referenced'}:
                    raise ValueError('A required source file was not verified; no playback file was exported.')
            if shutil.disk_usage(folder).free < _audio_working_allowance(audio_item) + _manifest_allowance(plan):
                raise ValueError('Not enough free space for playback preparation and temporary PCM files.')
            original_audio = _contained(original_root, audio_item.destination)
            with tempfile.TemporaryDirectory(prefix='.playback-', dir=folder) as temporary:
                temporary = Path(temporary)
                snapshots = {}
                for number, (source_item, source_record) in enumerate(sources.values()):
                    original = _contained(original_root, source_item.destination)
                    snapshots[source_item.source] = _snapshot_source(
                        original, temporary / f'source-{number}{original.suffix.lower()}',
                        source_item.size, source_record['sha256'], cancel=cancel,
                    )
                snapshot_audio = snapshots[audio_item.source]
                pcm = snapshot_audio
                if original_audio.suffix.lower() == '.mp3':
                    pcm = temporary / 'source.wav'
                    decode_mp3(snapshot_audio, pcm, cancel=cancel)
                music = temporary / 'music.wav'
                decoded_audio = None
                try:
                    decoded_audio = decode_disklavier_audio(pcm, music, cancel=cancel)
                except NotEncodedAudioError:
                    if pair is None:
                        raise
                    music = pcm

                if pair is not None:
                    ready = prepare_pair(pair, snapshots[item.source], music,
                                         cancel=cancel)
                    events = ready.events
                    music = ready.audio_path
                    offset, scale = ready.offset_seconds, ready.time_scale
                    details['alignment'] = ready.alignment_details
                else:
                    events = tuple((event.time_seconds, event.data) for event in decoded_audio.events)
                    offset, scale = 0.0, 1.0
                if decoded_audio is not None:
                    details['decode'] = decoded_audio.report
                check_cancel(cancel)
                for source_item, source_record in sources.values():
                    if _hash(_contained(original_root, source_item.destination), cancel) != source_record['sha256']:
                        raise ValueError('Original checksum changed during playback preparation')

                relative_stem = _audio_pair_stem(
                    folder, root, item.destination, reserved,
                    short_names=(audio_output != ENCODED_WAV and
                                 (generate_yamaha_catalogs or
                                  Path(item.source).parent.as_posix() in cataloged_sources)),
                    extensions=extensions,
                )
                paths = [Path(str(relative_stem) + extension) for extension in extensions]
                destinations = [_contained(folder, path.as_posix()) for path in paths]
                destinations[0].parent.mkdir(parents=True, exist_ok=True)
                for relative, extension in zip(paths, extensions):
                    derivative = {
                        'source': item.source, 'original': item.destination,
                        'audio_source': audio_item.source, 'destination': relative.as_posix(),
                        'kind': {'.mid': 'MIDI', '.mp3': 'MP3', '.wav': 'WAV'}[extension],
                        'conversion': f'Mark IV playback to {audio_output}', 'status': 'pending',
                    }
                    derivative_indices.append(len(manifest['derivatives']))
                    manifest['derivatives'].append(derivative)
                    journal.append('derivatives', derivative_indices[-1], derivative)
                if audio_output == ENCODED_WAV:
                    if pair is None and original_audio.suffix.lower() == '.wav':
                        # An already encoded WAV needs no additional lead-in or
                        # lossy round trip. Retain its verified original bytes.
                        publish_new_file(snapshot_audio, destinations[0],
                                         check_cancel=lambda: check_cancel(cancel))
                        exported = {'format': 'Original Yamaha encoded WAV', 'source_preserved': True}
                    else:
                        exported = encode_disklavier_wav(
                            events, music, destinations[0], midi_offset_seconds=offset,
                            midi_time_scale=scale, cancel=cancel,
                        )
                else:
                    exported = export_midi_mp3(
                        events, music, folder / relative_stem,
                        midi_offset_seconds=offset, midi_time_scale=scale, cancel=cancel,
                    )
                # Record complete published outputs even if cancellation arrives
                # immediately after publication.
                for derivative_index, destination in zip(derivative_indices, destinations):
                    derivative = manifest['derivatives'][derivative_index]
                    derivative.update(status='verified', size=destination.stat().st_size,
                                      sha256=_hash(destination))
                    journal.append('derivatives', derivative_index, derivative)
                details.update(status='prepared', destinations=[path.as_posix() for path in paths],
                               export=exported, hardware_verified=False)
                prepared += 1
        except NotEncodedAudioError as exc:
            details.update(status='skipped', reason=str(exc))
            skipped += 1
        except Exception as exc:
            interrupted = isinstance(exc, (CancelledError, InterruptedError, DisklavierEncodingCancelled)) or isinstance(
                getattr(exc, 'original_exception', None), InterruptedError)
            details.update(status='cancelled' if interrupted else 'failed', error=str(exc))
            recovery = getattr(exc, 'recovery_directory', '')
            retained = getattr(exc, 'retained_paths', ())
            if recovery or retained:
                details.update(recovery_directory=str(recovery), retained_paths=list(retained))
            if not interrupted or recovery or retained:
                manifest['errors'].append(f'{item.source}: Playback preparation failed: {exc}')
                failed += 1
            for derivative_index in derivative_indices:
                derivative = manifest['derivatives'][derivative_index]
                if derivative['status'] != 'verified':
                    derivative.update(status=details['status'], error=str(exc))
                    journal.append('derivatives', derivative_index, derivative)
            if interrupted:
                raise CancelledError('Cancelled by user') from None
        finally:
            journal.append('files', index, record)
            emit(item, completed + 1)
    return prepared, skipped, failed
