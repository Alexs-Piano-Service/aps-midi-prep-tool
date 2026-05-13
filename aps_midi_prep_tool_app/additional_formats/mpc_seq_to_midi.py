#!/usr/bin/env python3
"""
mpc_seq_to_midi.py

Best-effort converter for older Akai MPC .SEQ files, especially MPC3000 /
MPC60-family sequence files, to Standard MIDI File Type 1. It can also split
MPC .ALL files that contain multiple embedded .SEQ-style records.

This is intentionally dependency-free and reverse-engineered from a small batch
of .SEQ files. It is not a complete Akai file-format implementation.

What it currently does:
  - reads the sequence name and tempo from the header
  - splits MPC .ALL files into embedded sequence records
  - reads the MPC track table and track names
  - finds the main bar/time-signature run in the event stream
  - converts note events to separate MIDI tracks
  - writes tempo and time-signature meta events
  - maps likely drum/percussion tracks to MIDI channel 10

What it does not yet fully do:
  - preserve MPC sample/program assignments or actual sounds
  - preserve MPC note-variation parameters, mixer data, swing, etc.
  - guarantee all controller/program/pitch events are exported
  - guarantee correctness for every MPC OS/version or .SEQ variant

Usage:
  python mpc_seq_to_midi.py *.SEQ -o converted_midi
  python mpc_seq_to_midi.py COVER_ME.ALL -o converted_midi
  python mpc_seq_to_midi.py /path/to/seq_folder -o converted_midi --zip
  python mpc_seq_to_midi.py CELINE.SEQ --include-empty-tracks

The output .mid files should import into most DAWs as normal Type 1 MIDI files.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

PPQN = 96  # MPC3000/MPC60-family sequencer timing resolution
TRACK_TABLE_OFFSET = 0xCC

DRUM_NAME_HINTS = (
    "KICK", "SNARE", "HAT", "HH", "CLAP", "TAMB", "TOM", "COW",
    "CLAVE", "SHAKER", "CYM", "BLOCK", "STICK", "PERC", "RIM", "DRUM",
)


@dataclass
class TrackInfo:
    index: int             # zero-based order in table
    track_id: int          # MPC event track number, usually 1-based
    name: str
    meta_hex: str
    is_drum: bool
    midi_channel: int      # zero-based MIDI channel, 9 == channel 10


@dataclass(frozen=True)
class NoteEvent:
    tick: int
    track_id: int
    note: int
    velocity: int
    duration: int
    variation: int


@dataclass(frozen=True)
class Token:
    offset: int
    kind: str
    data: Tuple[int, ...]


@dataclass
class ConversionReport:
    input: str
    output: str
    sequence_name: str
    header_bars: int
    tempo_bpm: float
    table_tracks: int
    midi_tracks_written: int
    notes_written: int
    chosen_bar_run_start: int
    chosen_bar_run_end: int
    chosen_event_offset_hex: str
    warnings: List[str]


@dataclass(frozen=True)
class SequenceChunk:
    index: int
    name: str
    offset: int
    data: bytes


def clean_ascii(raw: bytes, fallback: str = "") -> str:
    """Decode Akai fixed-width strings. Keep printable characters only."""
    text = raw.split(b"\x00", 1)[0].decode("latin1", "replace")
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    text = text.strip()
    return text or fallback


def safe_filename(name: str, fallback: str = "sequence") -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" ._")
    return name or fallback


def read_seq_header(data: bytes) -> Tuple[str, int, float]:
    """Read the sequence name, approximate bar count, and tempo from the header."""
    seq_name = clean_ascii(data[9:25], "Sequence") if len(data) >= 25 else "Sequence"
    header_bars = data[0x1C] if len(data) > 0x1C else 0
    tempo_bpm = 120.0
    if len(data) >= 0x24:
        tempo_tenths = int.from_bytes(data[0x22:0x24], "little")
        if 200 <= tempo_tenths <= 3000:
            tempo_bpm = tempo_tenths / 10.0
    return seq_name, header_bars, tempo_bpm


def _looks_like_fixed_ascii_field(raw: bytes) -> bool:
    return (
        len(raw) == 16
        and raw[0] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
        and all(32 <= byte < 127 for byte in raw)
        and sum(chr(byte).isalnum() for byte in raw.rstrip()) >= 2
    )


def _mpc_sequence_header_name_at(data: bytes, start: int) -> Optional[str]:
    """Return the sequence name if a plausible MPC .SEQ header begins at start."""
    if start < 0 or start + 0x24 > len(data):
        return None
    raw_name = data[start + 9:start + 25]
    if not _looks_like_fixed_ascii_field(raw_name):
        return None

    header_bars = data[start + 0x1C]
    tempo_tenths = int.from_bytes(data[start + 0x22:start + 0x24], "little")
    if not (1 <= header_bars <= 255 and 200 <= tempo_tenths <= 3000):
        return None

    # These bytes are stable across the MPC .SEQ records found inside .ALL
    # files tested so far. They keep ordinary track names and event text from
    # being mistaken for a sequence header.
    if data[start + 0x1B] != 0:
        return None
    if data[start + 0x19] > 8 or data[start + 0x1A] > 16:
        return None
    if data[start + 0x20:start + 0x22] != b"\x00\x00":
        return None

    return clean_ascii(raw_name, "Sequence")


def find_embedded_sequence_offsets(data: bytes) -> List[Tuple[int, str]]:
    """Find likely .SEQ record starts inside an MPC .ALL file."""
    offsets: List[Tuple[int, str]] = []
    limit = max(0, len(data) - 0x24 + 1)
    for start in range(limit):
        name = _mpc_sequence_header_name_at(data, start)
        if name:
            offsets.append((start, name))
    return offsets


def extract_all_sequence_chunks(data: bytes) -> List[SequenceChunk]:
    """Split an MPC .ALL payload into embedded .SEQ-like chunks."""
    starts = find_embedded_sequence_offsets(data)
    chunks: List[SequenceChunk] = []
    for index, (start, name) in enumerate(starts, start=1):
        end = starts[index][0] if index < len(starts) else len(data)
        if end <= start:
            continue
        chunks.append(
            SequenceChunk(
                index=index,
                name=name,
                offset=start,
                data=data[start:end],
            )
        )
    return chunks


def looks_like_mpc_all_bytes(data: bytes) -> bool:
    return bool(extract_all_sequence_chunks(data))


def infer_drum_track(meta: bytes, name: str) -> bool:
    upper = name.upper()
    if any(hint in upper for hint in DRUM_NAME_HINTS):
        return True
    # In the v1 files tested here, byte 4 often marks drum/sample tracks.
    # Do not treat 0xff as a drum flag because v2-style tables often use 0xff broadly.
    if len(meta) >= 5 and meta[4] in (0x20, 0x40, 0x80):
        return True
    return False


def parse_track_table(data: bytes) -> Tuple[List[TrackInfo], int, List[str]]:
    """Parse the MPC track table beginning at 0xCC.

    The files examined use either 21-byte records or 24-byte records:
      v1-ish: [5 metadata bytes][16 byte track name]
      v2-ish: [5 metadata bytes][16 byte track name][3 extra bytes]
    A 0xff byte marks the end of the table.
    """
    warnings: List[str] = []
    if len(data) < TRACK_TABLE_OFFSET + 21:
        raise ValueError("File is too short to contain the expected MPC track table")

    # Files whose second byte is 0x02 in this batch use 24-byte table records.
    # Others use 21-byte records.
    record_len = 24 if len(data) > 1 and data[1] == 0x02 else 21

    tracks: List[TrackInfo] = []
    pos = TRACK_TABLE_OFFSET
    while pos + 21 <= len(data):
        if data[pos] == 0xFF:
            break
        meta = data[pos:pos + 5]
        raw_name = data[pos + 5:pos + 21]
        name = clean_ascii(raw_name, f"Track {len(tracks) + 1:02d}")
        track_id = meta[0] or (len(tracks) + 1)
        is_drum = infer_drum_track(meta, name)
        midi_channel = 9 if is_drum else ((track_id - 1) % 16)
        tracks.append(
            TrackInfo(
                index=len(tracks),
                track_id=track_id,
                name=name,
                meta_hex=meta.hex(),
                is_drum=is_drum,
                midi_channel=midi_channel,
            )
        )
        pos += record_len
        if len(tracks) > 512:
            warnings.append("Track table exceeded 512 entries; stopping parse to avoid runaway")
            break

    if not tracks:
        warnings.append("No track-table entries were found")
    return tracks, pos, warnings


def valid_bar_sig(bar: int, numerator: int, denominator: int) -> bool:
    return 1 <= bar <= 9999 and 1 <= numerator <= 32 and denominator in (1, 2, 4, 8, 16, 32)


def find_first_bar_marker(data: bytes, start: int) -> int:
    for i in range(max(0, start), max(0, len(data) - 4)):
        if data[i] != 0xA8:
            continue
        bar = data[i + 1] | (data[i + 2] << 8)
        num = data[i + 3]
        den = data[i + 4]
        if valid_bar_sig(bar, num, den):
            return i
    return start


def tokenize_event_stream(data: bytes, start: int = 0) -> Iterator[Token]:
    """Tokenize the event stream.

    Known opcodes from the test files:
      0x88: time advance, 3 bytes total, little-endian tick delta
      0x98: note event, 7 bytes total: track, note, velocity, variation, duration LE
      0xA8: bar/time-signature marker, 5 bytes total
      0xB0: control-change-like event, 4 bytes total
      0xC0: program-change-like event, 3 bytes total
      0xD0: channel-pressure-like event, 3 bytes total
      0xE0: pitch-bend-like event, 4 bytes total
      0xFF: sentinel/end byte

    Bytes below 0x80 are treated as one-byte tick deltas. In the files examined,
    the explicit 0x88 deltas and 0xA8 bar markers do most of the timing work.
    """
    i = max(0, start)
    n = len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            yield Token(i, "delta", (b,))
            i += 1
        elif b == 0x88 and i + 2 < n:
            yield Token(i, "delta", (data[i + 1] | (data[i + 2] << 8),))
            i += 3
        elif b == 0x98 and i + 6 < n:
            yield Token(i, "note", tuple(data[i + 1:i + 7]))
            i += 7
        elif b == 0xA8 and i + 4 < n:
            bar = data[i + 1] | (data[i + 2] << 8)
            yield Token(i, "bar", (bar, data[i + 3], data[i + 4]))
            i += 5
        elif b == 0xB0 and i + 3 < n:
            yield Token(i, "cc", tuple(data[i + 1:i + 4]))
            i += 4
        elif b == 0xC0 and i + 2 < n:
            yield Token(i, "program", tuple(data[i + 1:i + 3]))
            i += 3
        elif b == 0xD0 and i + 2 < n:
            yield Token(i, "pressure", tuple(data[i + 1:i + 3]))
            i += 3
        elif b == 0xE0 and i + 3 < n:
            yield Token(i, "pitch", tuple(data[i + 1:i + 4]))
            i += 4
        elif b == 0xFF:
            yield Token(i, "sentinel", ())
            i += 1
        else:
            # Unknown high byte. Skip one byte so the parser can resynchronize.
            yield Token(i, "unknown", (b,))
            i += 1


def note_token_is_plausible(token: Token, track_ids: set[int]) -> bool:
    if token.kind != "note" or len(token.data) != 6:
        return False
    track_id, note, velocity, _variation, dur_lo, dur_hi = token.data
    duration = dur_lo | (dur_hi << 8)
    return (
        track_id in track_ids
        and 0 <= note <= 127
        and 1 <= velocity <= 127
        and 1 <= duration <= 65535
    )


def collect_bar_runs(tokens: Sequence[Token]) -> List[List[Token]]:
    runs: List[List[Token]] = []
    current: List[Token] = []
    prev_bar: Optional[int] = None
    for tok in tokens:
        if tok.kind != "bar":
            continue
        bar, num, den = tok.data
        if not valid_bar_sig(bar, num, den):
            continue
        if current and prev_bar is not None and bar == prev_bar + 1:
            current.append(tok)
        else:
            if current:
                runs.append(current)
            current = [tok]
        prev_bar = bar
    if current:
        runs.append(current)
    return runs


def count_plausible_notes(tokens: Sequence[Token], start_offset: int, end_offset: Optional[int], track_ids: set[int]) -> int:
    count = 0
    for tok in tokens:
        if tok.offset < start_offset:
            continue
        if end_offset is not None and tok.offset >= end_offset:
            break
        if note_token_is_plausible(tok, track_ids):
            count += 1
    return count


def choose_main_bar_run(tokens: Sequence[Token], track_ids: set[int]) -> Tuple[List[Token], int]:
    """Choose the most likely real sequence pass.

    Some files contain short stale/preview bar runs before the actual sequence.
    The best heuristic across the uploaded examples is: prefer valid bar runs
    that contain plausible note events after them, then prefer longer runs.
    """
    runs = collect_bar_runs(tokens)
    if not runs:
        raise ValueError("No valid bar/time-signature markers were found")

    best_run: Optional[List[Token]] = None
    best_score: Tuple[int, int, int, int, int] = (-1, -1, -1, -1, -1)
    for idx, run in enumerate(runs):
        start = run[0].offset
        end = runs[idx + 1][0].offset if idx + 1 < len(runs) else None
        notes_after = count_plausible_notes(tokens, start, end, track_ids)
        starts_at_one = 1 if run[0].data[0] == 1 else 0
        # Score fields: has notes, length, starts at bar 1, note count, earlier/later tiebreak.
        # The final offset tiebreak makes later long duplicate bar-runs win over tiny lead-ins.
        score = (1 if notes_after > 0 else 0, len(run), starts_at_one, notes_after, start)
        if score > best_score:
            best_score = score
            best_run = run

    if best_run is None:
        best_run = max(runs, key=len)
    start_offset = best_run[0].offset
    return best_run, start_offset


def build_bar_start_ticks(bar_run: Sequence[Token]) -> Dict[int, int]:
    """Build tick offsets for each bar from the chosen bar/time-signature markers."""
    if not bar_run:
        return {1: 0}
    starts: Dict[int, int] = {}
    tick = 0
    prev_bar: Optional[int] = None
    prev_num = 4
    prev_den = 4

    for tok in bar_run:
        bar, num, den = tok.data
        if prev_bar is None:
            tick = 0
        else:
            # Fill any missing bars using the previous time signature.
            bar_len = int(round(PPQN * 4 * prev_num / prev_den))
            for missing in range(prev_bar + 1, bar):
                tick += bar_len
                starts[missing] = tick
            tick += bar_len
        starts[bar] = tick
        prev_bar = bar
        prev_num, prev_den = num, den
    return starts


def extract_notes(data: bytes) -> Tuple[str, int, float, List[TrackInfo], List[NoteEvent], List[Tuple[int, int, int]], List[str], Tuple[int, int, int]]:
    """Return parsed notes and time-signature changes.

    Returns:
      sequence_name, header_bars, tempo_bpm, tracks, notes, sig_changes, warnings,
      chosen_run_summary=(start_bar, end_bar, start_offset)
    """
    seq_name, header_bars, tempo_bpm = read_seq_header(data)
    tracks, table_end, warnings = parse_track_table(data)
    track_ids = {t.track_id for t in tracks}

    event_start = find_first_bar_marker(data, table_end)
    tokens = list(tokenize_event_stream(data, event_start))
    if not tokens:
        raise ValueError("No event stream tokens were found")
    bar_run, start_offset = choose_main_bar_run(tokens, track_ids)
    bar_starts = build_bar_start_ticks(bar_run)
    start_bar = bar_run[0].data[0]
    end_bar = bar_run[-1].data[0]

    # Keep only actual time-signature changes from the chosen run.
    sig_changes: List[Tuple[int, int, int]] = []
    last_sig: Optional[Tuple[int, int]] = None
    for tok in bar_run:
        bar, num, den = tok.data
        if (num, den) != last_sig:
            sig_changes.append((bar_starts.get(bar, 0), num, den))
            last_sig = (num, den)

    notes: List[NoteEvent] = []
    seen_notes: set[Tuple[int, int, int, int, int]] = set()
    current_tick = 0
    active = False
    for tok in tokens:
        if tok.offset < start_offset:
            continue
        if tok.kind == "bar":
            bar, num, den = tok.data
            if valid_bar_sig(bar, num, den) and bar in bar_starts:
                current_tick = bar_starts[bar]
                active = True
            continue
        if not active:
            continue
        if tok.kind == "delta":
            current_tick += tok.data[0]
        elif tok.kind == "note":
            track_id, note, velocity, variation, dur_lo, dur_hi = tok.data
            duration = dur_lo | (dur_hi << 8)
            if track_id not in track_ids:
                continue
            if not (0 <= note <= 127 and 1 <= velocity <= 127 and duration > 0):
                continue
            key = (current_tick, track_id, note, velocity, duration)
            if key in seen_notes:
                continue
            seen_notes.add(key)
            notes.append(NoteEvent(current_tick, track_id, note, velocity, duration, variation))

    if not notes:
        warnings.append("No plausible notes were extracted")
    if len(bar_run) < max(1, header_bars // 4) and header_bars:
        warnings.append(
            f"Chosen bar run has {len(bar_run)} bars but header suggests {header_bars}; file may need parser tuning"
        )
    return seq_name, header_bars, tempo_bpm, tracks, notes, sig_changes, warnings, (start_bar, end_bar, start_offset)


# ---- Minimal Standard MIDI File writer ------------------------------------

def vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("VLQ value cannot be negative")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def midi_chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + len(payload).to_bytes(4, "big") + payload


def meta_event(delta: int, meta_type: int, payload: bytes) -> bytes:
    return vlq(delta) + bytes([0xFF, meta_type]) + vlq(len(payload)) + payload


def track_chunk(events: List[Tuple[int, bytes]]) -> bytes:
    """Build a MIDI track chunk from absolute-time events."""
    events.sort(key=lambda x: x[0])
    payload = bytearray()
    last_tick = 0
    for tick, event_bytes in events:
        tick = max(0, int(tick))
        payload += vlq(tick - last_tick)
        payload += event_bytes
        last_tick = tick
    payload += meta_event(0, 0x2F, b"")  # end of track
    return midi_chunk(b"MTrk", bytes(payload))


def tempo_payload(bpm: float) -> bytes:
    bpm = bpm if 20 <= bpm <= 300 else 120.0
    micros_per_quarter = int(round(60_000_000 / bpm))
    return micros_per_quarter.to_bytes(3, "big")


def time_signature_payload(numerator: int, denominator: int) -> bytes:
    if denominator <= 0 or denominator & (denominator - 1):
        denominator = 4
    denom_power = int(math.log2(denominator))
    return bytes([numerator & 0xFF, denom_power & 0xFF, 24, 8])


def text_payload(text: str, max_len: int = 127) -> bytes:
    return clean_ascii(text.encode("latin1", "replace"), "Track").encode("latin1", "replace")[:max_len]


def write_midi(
    output_path: Path,
    sequence_name: str,
    tempo_bpm: float,
    tracks: Sequence[TrackInfo],
    notes: Sequence[NoteEvent],
    sig_changes: Sequence[Tuple[int, int, int]],
    include_empty_tracks: bool = False,
) -> int:
    """Write a Standard MIDI File Type 1. Returns number of MIDI note tracks."""
    track_by_id = {t.track_id: t for t in tracks}

    chunks: List[bytes] = []

    # Conductor/meta track
    conductor_events: List[Tuple[int, bytes]] = []
    conductor_events.append((0, bytes([0xFF, 0x03]) + vlq(len(text_payload(sequence_name))) + text_payload(sequence_name)))
    conductor_events.append((0, bytes([0xFF, 0x51, 0x03]) + tempo_payload(tempo_bpm)))
    if sig_changes:
        for tick, num, den in sig_changes:
            payload = time_signature_payload(num, den)
            conductor_events.append((tick, bytes([0xFF, 0x58, len(payload)]) + payload))
    else:
        payload = time_signature_payload(4, 4)
        conductor_events.append((0, bytes([0xFF, 0x58, len(payload)]) + payload))
    chunks.append(track_chunk(conductor_events))

    # Notes grouped per MPC track id
    notes_by_track: Dict[int, List[NoteEvent]] = {}
    for n in notes:
        notes_by_track.setdefault(n.track_id, []).append(n)

    midi_note_tracks_written = 0
    for tr in tracks:
        tr_notes = notes_by_track.get(tr.track_id, [])
        if not tr_notes and not include_empty_tracks:
            continue
        events: List[Tuple[int, bytes]] = []
        name_bytes = text_payload(tr.name)
        events.append((0, bytes([0xFF, 0x03]) + vlq(len(name_bytes)) + name_bytes))
        channel = max(0, min(15, tr.midi_channel))
        for n in tr_notes:
            note = max(0, min(127, n.note))
            vel = max(1, min(127, n.velocity))
            start = max(0, n.tick)
            end = max(start + 1, start + n.duration)
            # Sort note-offs before note-ons at the same absolute tick by using status bytes later.
            events.append((start, bytes([0x90 | channel, note, vel])))
            events.append((end, bytes([0x80 | channel, note, 0])))
        # Custom stable ordering within the same tick: track name first, note-offs before note-ons.
        events.sort(key=lambda x: (x[0], 0 if x[1].startswith(b"\xff\x03") else (1 if x[1][0] & 0xF0 == 0x80 else 2)))
        chunks.append(track_chunk(events))
        midi_note_tracks_written += 1

    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(chunks).to_bytes(2, "big") + PPQN.to_bytes(2, "big")
    output_path.write_bytes(header + b"".join(chunks))
    return midi_note_tracks_written


def convert_one(
    input_path: Path,
    output_dir: Path,
    include_empty_tracks: bool = False,
    output_stem: Optional[str] = None,
) -> ConversionReport:
    data = input_path.read_bytes()
    return convert_bytes(
        data,
        source_label=str(input_path),
        output_dir=output_dir,
        include_empty_tracks=include_empty_tracks,
        output_stem=output_stem if output_stem is not None else input_path.stem,
    )


def convert_bytes(
    data: bytes,
    source_label: str,
    output_dir: Path,
    include_empty_tracks: bool = False,
    output_stem: Optional[str] = None,
) -> ConversionReport:
    seq_name, header_bars, tempo_bpm, tracks, notes, sig_changes, warnings, run_summary = extract_notes(data)
    stem = safe_filename(output_stem if output_stem is not None else seq_name)
    output_path = output_dir / f"{stem}.mid"
    output_dir.mkdir(parents=True, exist_ok=True)
    midi_tracks_written = write_midi(
        output_path,
        seq_name,
        tempo_bpm,
        tracks,
        notes,
        sig_changes,
        include_empty_tracks=include_empty_tracks,
    )
    start_bar, end_bar, start_offset = run_summary
    return ConversionReport(
        input=source_label,
        output=str(output_path),
        sequence_name=seq_name,
        header_bars=header_bars,
        tempo_bpm=tempo_bpm,
        table_tracks=len(tracks),
        midi_tracks_written=midi_tracks_written,
        notes_written=len(notes),
        chosen_bar_run_start=start_bar,
        chosen_bar_run_end=end_bar,
        chosen_event_offset_hex=hex(start_offset),
        warnings=warnings,
    )


def convert_all(
    input_path: Path,
    output_dir: Path,
    include_empty_tracks: bool = False,
    output_stem: Optional[str] = None,
    include_empty_sequences: bool = False,
) -> Tuple[List[ConversionReport], List[str]]:
    data = input_path.read_bytes()
    chunks = extract_all_sequence_chunks(data)
    if not chunks:
        raise ValueError("No embedded MPC sequences were found in this .ALL file")

    reports: List[ConversionReport] = []
    warnings: List[str] = []
    skipped_empty = 0
    container_stem = safe_filename(output_stem if output_stem is not None else input_path.stem, "all")
    for chunk in chunks:
        chunk_name = safe_filename(chunk.name, f"sequence_{chunk.index:02d}")
        chunk_stem = f"{container_stem}_{chunk.index:02d}_{chunk_name}"
        source_label = f"{input_path}:{hex(chunk.offset)} {chunk.name}"
        try:
            report = convert_bytes(
                chunk.data,
                source_label=source_label,
                output_dir=output_dir,
                include_empty_tracks=include_empty_tracks,
                output_stem=chunk_stem,
            )
            if report.notes_written <= 0 and not include_empty_sequences:
                skipped_empty += 1
                try:
                    Path(report.output).unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 - keep converting other embedded records
            warnings.append(f"{chunk.name} at {hex(chunk.offset)}: {exc}")

    if skipped_empty:
        warnings.append(f"Skipped {skipped_empty} embedded sequence slot(s) with no note data.")
    return reports, warnings


def expand_inputs(paths: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.glob("*.SEQ")))
            out.extend(sorted(p.glob("*.seq")))
            out.extend(sorted(p.glob("*.ALL")))
            out.extend(sorted(p.glob("*.all")))
        else:
            matches = sorted(Path().glob(raw)) if any(ch in raw for ch in "*?[") else [p]
            out.extend(matches)
    # Deduplicate while preserving order.
    seen = set()
    unique: List[Path] = []
    for p in out:
        rp = str(p.resolve()) if p.exists() else str(p)
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Best-effort Akai MPC .SEQ/.ALL to Standard MIDI Type 1 converter")
    parser.add_argument("inputs", nargs="+", help=".SEQ or .ALL files, directories containing them, or shell globs")
    parser.add_argument("-o", "--output-dir", default="converted_midi", help="Output directory for .mid files")
    parser.add_argument("--include-empty-tracks", action="store_true", help="Write MIDI tracks even if no notes were found on them")
    parser.add_argument("--include-empty-sequences", action="store_true", help="For .ALL files, also write embedded sequence slots with no notes")
    parser.add_argument("--report", default=None, help="Write JSON conversion report to this path")
    parser.add_argument("--zip", dest="zip_path", default=None, help="Optionally zip the output directory to this path")
    args = parser.parse_args(argv)

    inputs = expand_inputs(args.inputs)
    if not inputs:
        print("No input .SEQ or .ALL files found", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    reports: List[ConversionReport] = []
    failures: List[Dict[str, str]] = []
    for inp in inputs:
        try:
            if inp.suffix.lower() == ".all":
                all_reports, all_warnings = convert_all(
                    inp,
                    output_dir,
                    include_empty_tracks=args.include_empty_tracks,
                    include_empty_sequences=args.include_empty_sequences,
                )
                reports.extend(all_reports)
                print(f"{inp.name}: {len(all_reports)} embedded sequence(s) -> {output_dir}")
                for report in all_reports:
                    print(
                        f"  {report.sequence_name}: {report.notes_written} notes, "
                        f"{report.midi_tracks_written} MIDI tracks, tempo {report.tempo_bpm:.1f} BPM "
                        f"-> {Path(report.output).name}"
                    )
                    for warning in report.warnings:
                        print(f"    warning: {warning}")
                for warning in all_warnings:
                    print(f"  warning: {warning}")
            else:
                report = convert_one(inp, output_dir, include_empty_tracks=args.include_empty_tracks)
                reports.append(report)
                print(
                    f"{inp.name}: {report.notes_written} notes, {report.midi_tracks_written} MIDI tracks, "
                    f"tempo {report.tempo_bpm:.1f} BPM -> {Path(report.output).name}"
                )
                if report.warnings:
                    for warning in report.warnings:
                        print(f"  warning: {warning}")
        except Exception as exc:  # noqa: BLE001 - CLI should continue on other files
            failures.append({"input": str(inp), "error": str(exc)})
            print(f"ERROR converting {inp}: {exc}", file=sys.stderr)

    if args.report:
        report_payload = {
            "converter": "mpc_seq_to_midi.py",
            "ppqn": PPQN,
            "reports": [asdict(r) for r in reports],
            "failures": failures,
        }
        Path(args.report).write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        print(f"Wrote report: {args.report}")

    if args.zip_path:
        zip_path = Path(args.zip_path)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for mid in sorted(output_dir.glob("*.mid")):
                zf.write(mid, arcname=mid.name)
            if args.report and Path(args.report).exists():
                zf.write(args.report, arcname=Path(args.report).name)
        print(f"Wrote zip: {zip_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
