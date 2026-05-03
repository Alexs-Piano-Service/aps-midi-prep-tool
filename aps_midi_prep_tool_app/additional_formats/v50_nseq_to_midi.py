#!/usr/bin/env python3
"""Extract Yamaha V50 embedded NSEQ songs and convert them to MIDI.

This is intended for Yamaha V50 720K floppy images like the supplied
`therian27.img`, and for extracted V50 ALL files that contain `V50SEQV1`
sequence slots.

What it does:
  * Reads the V50/Yamaha FAT12-like 720K floppy layout, even when sector 0 is
    not a normal DOS boot sector.
  * Extracts active root-directory files.
  * Finds V50SEQV1 song slots inside V50 ALL files.
  * Extracts raw song-slot bytes, raw concatenated NSEQ, and individual raw
    NSEQ tracks.
  * Converts decoded NSEQ note/control/program/pitch/aftertouch events to
    Standard MIDI File format 1.
  * Adds optional DAW-friendly General MIDI fallback program changes when a
    V50/NSEQ track contains notes but no native program-change events, avoiding
    the common "everything opens as Acoustic Grand Piano" default.
  * Writes a JSON sidecar preserving the Yamaha-specific context: slot header,
    routing/channel bytes, tempo bytes, raw-file hashes, program-change groups,
    and warnings.  The sidecar + raw files are the preservation layer; the MIDI
    file is the DAW-friendly playback/export layer.

The conversion is intentionally conservative.  It does not decode V50 FM voice
or performance data, because that information lives elsewhere in the V50 ALL
file.  The ALL file and raw slot bytes are therefore preserved so that voice and
performance sections can be decoded later without needing the original floppy.

Usage examples:

    python3 v50_nseq_to_midi_v2.py therian27.img out_v50
    python3 v50_nseq_to_midi_v2.py --include-events-json therian27.img out_v50_verbose
    python3 v50_nseq_to_midi_v2.py --mode file extracted_file.V09 out_v50
    python3 v50_nseq_to_midi_v2.py --mode raw-nseq song.raw_nseq out_v50
    python3 v50_nseq_to_midi_v2.py --program-mode gm --initial-programs "1=piano,2=bass,3=strings" therian27.img out_v50_gm

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# ---------------------------------------------------------------------------
# V50 floppy layout inferred from the supplied disk image and common 720K 2DD
# FAT12 geometry.  The first sector can be non-DOS-looking, but the FAT/root
# data begin at these offsets.
# ---------------------------------------------------------------------------

BYTES_PER_SECTOR = 512
SECTORS_PER_CLUSTER = 2
RESERVED_SECTORS = 1
FAT_COUNT = 2
SECTORS_PER_FAT = 3
ROOT_ENTRIES = 112
CLUSTER_SIZE = BYTES_PER_SECTOR * SECTORS_PER_CLUSTER
FAT_OFFSET = RESERVED_SECTORS * BYTES_PER_SECTOR
ROOT_OFFSET = (RESERVED_SECTORS + FAT_COUNT * SECTORS_PER_FAT) * BYTES_PER_SECTOR
DATA_OFFSET = ROOT_OFFSET + ROOT_ENTRIES * 32

V50SEQ_SIGNATURE = b"V50SEQV1"
V50SEQ_HEADER_LEN = 0x22  # 34 bytes: signature + config + name + routing + flags

NSEQ_TIME_UNITS_PER_QUARTER = 96      # NSEQ time delta unit = 1/384 whole note
NSEQ_DURATION_UNITS_PER_QUARTER = 24  # NSEQ note duration unit = 1/96 whole note

_BAD_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ +\-()]")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(text: str, default: str = "unnamed") -> str:
    text = text.replace("\x00", "").strip()
    text = _BAD_NAME_CHARS.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text or default


def latin1_clean(bs: bytes) -> str:
    """Decode Yamaha/FAT text fields while keeping odd bytes visible."""
    return bs.decode("latin-1", "replace").rstrip(" \x00")


def hex_bytes(data: bytes) -> str:
    return data.hex(" ")


def relpath(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def units_to_ticks(units: int, units_per_quarter: int, ppq: int) -> int:
    # Use integer rounding so arbitrary PPQ values still work.
    return int(round(units * ppq / units_per_quarter))


# ---------------------------------------------------------------------------
# DAW-friendly General MIDI fallback helpers
# ---------------------------------------------------------------------------

GM_PROGRAM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

PROGRAM_ALIASES = {
    "piano": 0,
    "grand": 0,
    "bright piano": 1,
    "electric piano": 4,
    "ep": 4,
    "e.piano": 4,
    "dx ep": 5,
    "organ": 16,
    "rock organ": 18,
    "guitar": 25,
    "steel guitar": 25,
    "clean guitar": 27,
    "bass": 33,
    "acoustic bass": 32,
    "upright bass": 32,
    "wood bass": 32,
    "strings": 48,
    "string ensemble": 48,
    "ensemble": 48,
    "choir": 52,
    "brass": 61,
    "synth brass": 62,
    "sax": 65,
    "saxophone": 65,
    "flute": 73,
    "clarinet": 71,
    "trumpet": 56,
    "pad": 88,
    "warm pad": 89,
    "sweep pad": 95,
    "lead": 80,
    "synth lead": 80,
    "bells": 14,
    "vibes": 11,
    "marimba": 12,
}


def gm_program_display(program: int | None) -> str | None:
    if program is None:
        return None
    p = int(program) & 0x7F
    return f"{p + 1}: {GM_PROGRAM_NAMES[p]}"


def guess_gm_program_from_text(text: str | None) -> tuple[int | None, str | None]:
    """Return a zero-based GM program guess from a V50 voice/performance name.

    This is deliberately a DAW-playback heuristic, not a claim that the V50 FM
    voice is actually General MIDI.  The raw Yamaha data is still preserved.
    """
    if not text:
        return None, None
    original = text.strip()
    t = original.lower()
    t = re.sub(r"[^a-z0-9/+ ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    compact = t.replace(" ", "")

    # Order matters: detect electric pianos before generic pianos, synth brass
    # before brass, etc.
    patterns: list[tuple[tuple[str, ...], int, str]] = [
        (("wdbs", "woodbass", "wood bass", "upright", "ac bass", "acoustic bass"), 32, "name suggests acoustic/upright bass"),
        (("slap",), 36, "name suggests slap bass"),
        (("synthbass", "synbass", "s bass", "s/bass"), 38, "name suggests synth bass"),
        (("bass", "bs/", "/bs", " bs"), 33, "name suggests electric bass"),
        (("balladep", "e piano", "e.piano", "epno", "epn", " dx ", "dxep", " tine", "road", "rhodes"), 4, "name suggests electric piano"),
        (("clav",), 7, "name suggests clavinet"),
        (("grand", "piano", "pf", "pno"), 0, "name suggests piano"),
        (("drawbar", "organ", "org"), 16, "name suggests organ"),
        (("12st", "steelgtr", "steel guitar", "acgtr", "acoustic guitar"), 25, "name suggests steel/acoustic guitar"),
        (("jazzgtr", "jazz guitar"), 26, "name suggests jazz guitar"),
        (("cleangtr", "clean guitar", "egtr", "e.gtr"), 27, "name suggests clean electric guitar"),
        (("guitar", "gtr"), 25, "name suggests guitar"),
        (("pizz",), 45, "name suggests pizzicato strings"),
        (("tremolo",), 44, "name suggests tremolo strings"),
        (("strings", "strgs", "string", "ensmble", "ensemble"), 48, "name suggests string ensemble"),
        (("choir", "aahs", "oohs", "voice"), 52, "name suggests choir/voice"),
        (("synthbrass", "synbrass", "s brass"), 62, "name suggests synth brass"),
        (("brass", "horns"), 61, "name suggests brass section"),
        (("trumpet",), 56, "name suggests trumpet"),
        (("trombone",), 57, "name suggests trombone"),
        (("sax",), 65, "name suggests saxophone"),
        (("clarinet",), 71, "name suggests clarinet"),
        (("flute",), 73, "name suggests flute"),
        (("oboe",), 68, "name suggests oboe"),
        (("vibes", "vibe"), 11, "name suggests vibraphone"),
        (("marimba",), 12, "name suggests marimba"),
        (("glock",), 9, "name suggests glockenspiel"),
        (("bell", "bells"), 14, "name suggests bells"),
        (("sweeppd", "sweep pad", "sweep", "sweepad"), 95, "name suggests sweep pad"),
        (("analgpad", "analogpad", "analg pad", "warm pad", "warmpad"), 89, "name suggests warm/analog pad"),
        (("pad", "pd", "polysynth", "poly synth"), 88, "name suggests pad"),
        (("lead", "solo", "saw", "square"), 80, "name suggests synth lead"),
        (("metal", "metallic"), 93, "name suggests metallic pad"),
        (("space", "universe", "atmos", "crystal"), 98, "name suggests atmospheric/crystal FX"),
    ]
    for needles, program, reason in patterns:
        for needle in needles:
            n = needle.replace(" ", "")
            if needle in t or n in compact:
                return program, f"{reason}: {original!r}"

    alias_program = PROGRAM_ALIASES.get(t)
    if alias_program is not None:
        return alias_program, f"manual alias matched: {original!r}"
    return None, None


def parse_program_value(value: str) -> int:
    """Parse a program value. Numeric values are GM 1-based unless 0 is used."""
    v = value.strip()
    if not v:
        raise ValueError("empty program value")
    if re.fullmatch(r"\d+", v):
        n = int(v)
        if n == 0:
            return 0
        if 1 <= n <= 128:
            return n - 1
        raise ValueError(f"program number {n} is outside 0 or 1..128")
    guessed, reason = guess_gm_program_from_text(v)
    if guessed is None:
        known = ", ".join(sorted(PROGRAM_ALIASES)[:12])
        raise ValueError(f"unknown program name {value!r}; examples: {known}, ...")
    return guessed


def parse_initial_program_overrides(spec: str | None) -> dict[str, dict[int, int]]:
    """Parse --initial-programs.

    Examples:
      track1=1,ch2=33,3=strings
      1=piano,2=bass,3=warm pad

    Bare numbers before '=' apply to both track number and channel number, using
    one-based numbering.  Program numbers are GM 1-based except 0 is accepted as
    Acoustic Grand Piano for script-style users.
    """
    out: dict[str, dict[int, int]] = {"track": {}, "channel": {}}
    if not spec:
        return out
    for raw_part in re.split(r"[,;]", spec):
        part = raw_part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
        elif ":" in part:
            key, val = part.split(":", 1)
        else:
            raise ValueError(f"program override {part!r} needs '=' or ':'")
        key_l = key.strip().lower().replace(" ", "")
        program = parse_program_value(val)
        m = re.fullmatch(r"(?:track|tr|t)(\d+)", key_l)
        if m:
            out["track"][int(m.group(1))] = program
            continue
        m = re.fullmatch(r"(?:channel|chan|ch|c)(\d+)", key_l)
        if m:
            out["channel"][int(m.group(1))] = program
            continue
        m = re.fullmatch(r"\d+", key_l)
        if m:
            one_based = int(key_l)
            out["track"][one_based] = program
            out["channel"][one_based] = program
            continue
        raise ValueError(f"unknown override key {key!r}; use track1, ch1, or 1")
    return out


def is_useful_v50_name(name: str | None) -> bool:
    if not name:
        return False
    stripped = name.strip()
    if not stripped:
        return False
    if set(stripped) <= {"$", " ", "\x00"}:
        return False
    if stripped.upper() in {"INIT VOICE", "INIT PERF", "INITPERF"}:
        return False
    # Require at least two alphanumeric characters so binary noise does not look
    # like a voice/performance name.
    return len(re.findall(r"[A-Za-z0-9]", stripped)) >= 2


def extract_v50_primary_name_table(filedata: bytes) -> list[dict[str, Any]]:
    """Best-effort extraction of likely V50 performance/voice names.

    In the supplied V50 ALL files, a clean 10-byte name appears at 0x0440 +
    0x80*n + 2 for many entries.  This table is not treated as authoritative;
    it is used only to choose DAW-friendly GM fallback patches and is preserved
    in sidecar JSON for later format work.
    """
    out: list[dict[str, Any]] = []
    base = 0x0440
    stride = 0x80
    for index in range(100):
        off = base + stride * index + 2
        if off + 10 > len(filedata):
            break
        raw = filedata[off : off + 10]
        # Decode printable ASCII/Latin-1 and strip null/space padding.
        name = "".join(chr(b) if 32 <= b <= 126 else " " for b in raw).strip()
        if not is_useful_v50_name(name):
            continue
        gm, reason = guess_gm_program_from_text(name)
        out.append(
            {
                "index_zero_based": index,
                "index_one_based": index + 1,
                "offset_hex": f"0x{off:X}",
                "name": name,
                "inferred_gm_program_zero_based": gm,
                "inferred_gm_program_one_based": None if gm is None else gm + 1,
                "inferred_gm_name": None if gm is None else GM_PROGRAM_NAMES[gm],
                "inference_reason": reason,
            }
        )
    return out


def choose_v50_name_shift(name_table: list[dict[str, Any]]) -> int:
    """Choose whether track 1 should use table entry 0 or 1.

    Some files appear to have a song/performance label at entry 0 and then part
    names after it.  If entry 0 is not musically mappable but entry 1 is, shift
    the track-to-name lookup by one.
    """
    by_index = {int(e["index_zero_based"]): e for e in name_table}
    e0 = by_index.get(0)
    e1 = by_index.get(1)
    if not e0 or not e1:
        return 0
    n0 = str(e0.get("name", "")).lower()
    gm0 = e0.get("inferred_gm_program_zero_based")
    gm1 = e1.get("inferred_gm_program_zero_based")
    if gm0 is None and gm1 is not None:
        return 1
    if ("seq" in n0 or "song" in n0) and gm1 is not None:
        return 1
    return 0


def first_note_time_units(parsed: "NSeqParseResult") -> int | None:
    times = [ev.time_units for ev in parsed.events if ev.kind == "note"]
    return min(times) if times else None


def has_native_program_change(parsed: "NSeqParseResult") -> bool:
    return any(ev.kind == "program_change" for ev in parsed.events)


def track_note_stats(parsed: "NSeqParseResult") -> dict[str, Any]:
    notes = [ev for ev in parsed.events if ev.kind == "note"]
    if not notes:
        return {"note_count": 0}
    pitches = [int(ev.fields["note"]) for ev in notes]
    durations = [int(ev.fields.get("duration_units", 0)) for ev in notes]
    return {
        "note_count": len(notes),
        "min_note": min(pitches),
        "max_note": max(pitches),
        "average_note": sum(pitches) / len(pitches),
        "average_duration_units": sum(durations) / max(1, len(durations)),
        "pitch_bend_count": sum(1 for ev in parsed.events if ev.kind == "pitch_bend"),
        "control_change_count": sum(1 for ev in parsed.events if ev.kind == "control_change"),
    }


def guess_gm_program_from_track_stats(parsed: "NSeqParseResult") -> tuple[int | None, str | None]:
    stats = track_note_stats(parsed)
    if not stats.get("note_count"):
        return None, "no notes on this track"
    avg = float(stats["average_note"])
    min_note = int(stats["min_note"])
    pitch_bends = int(stats.get("pitch_bend_count", 0))
    track_no = parsed.track_number

    if avg < 45 or min_note < 36:
        return 33, "track statistics suggest bass range"
    if pitch_bends > 20 and avg >= 55:
        return 80, "frequent pitch bend suggests lead/synth part"
    # Conservative defaults by track number for V50 8-track sequences.
    defaults = [0, 33, 48, 25, 61, 80, 88, 52]
    program = defaults[track_no % len(defaults)]
    return program, f"fallback by V50 track number {track_no + 1} and note statistics"


def choose_initial_program_info(
    *,
    parsed: "NSeqParseResult",
    midi_channel: int,
    program_mode: str,
    overrides: dict[str, dict[int, int]],
    v50_name_table: list[dict[str, Any]],
    v50_name_shift: int,
) -> dict[str, Any] | None:
    """Choose a GM fallback program-change event for the start of a track."""
    if program_mode == "none":
        return None

    track_one = parsed.track_number + 1
    forced = None
    source = None
    if overrides.get("track", {}).get(track_one) is not None:
        forced = overrides["track"][track_one]
        source = f"--initial-programs track{track_one}"
    elif overrides.get("channel", {}).get(midi_channel) is not None:
        forced = overrides["channel"][midi_channel]
        source = f"--initial-programs ch{midi_channel}"
    if forced is not None:
        p = int(forced) & 0x7F
        return {
            "midi_program_zero_based": p,
            "midi_program_one_based": p + 1,
            "gm_name": GM_PROGRAM_NAMES[p],
            "source": source,
            "reason": "user override",
        }

    if program_mode == "preserve":
        return None
    if program_mode == "gm-fallback" and has_native_program_change(parsed):
        return None

    if not any(ev.kind == "note" for ev in parsed.events):
        return None

    by_index = {int(e["index_zero_based"]): e for e in v50_name_table}
    name_entry = by_index.get(parsed.track_number + v50_name_shift)
    if name_entry and name_entry.get("inferred_gm_program_zero_based") is not None:
        p = int(name_entry["inferred_gm_program_zero_based"]) & 0x7F
        return {
            "midi_program_zero_based": p,
            "midi_program_one_based": p + 1,
            "gm_name": GM_PROGRAM_NAMES[p],
            "source": "v50_name_table_0x0440",
            "v50_name": name_entry.get("name"),
            "v50_name_index_zero_based": name_entry.get("index_zero_based"),
            "v50_name_lookup_shift": v50_name_shift,
            "reason": name_entry.get("inference_reason"),
        }

    p, reason = guess_gm_program_from_track_stats(parsed)
    if p is None:
        return None
    return {
        "midi_program_zero_based": int(p) & 0x7F,
        "midi_program_one_based": (int(p) & 0x7F) + 1,
        "gm_name": GM_PROGRAM_NAMES[int(p) & 0x7F],
        "source": "track_statistics",
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# FAT12-ish V50 disk extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirEntry:
    index: int
    raw_name: bytes
    name: str
    ext: str
    attr: int
    start_cluster: int
    size: int
    deleted: bool

    @property
    def display_name(self) -> str:
        return f"{self.name}.{self.ext}" if self.ext else self.name

    @property
    def safe_filename(self) -> str:
        base = safe_name(self.name)
        ext = safe_name(self.ext, "")
        return f"{self.index:02d}_{base}.{ext}" if ext else f"{self.index:02d}_{base}"


def fat12_entry(fat: bytes, cluster: int) -> int:
    i = cluster + (cluster // 2)
    if i + 1 >= len(fat):
        return 0xFFF
    if cluster & 1:
        return ((fat[i] >> 4) | (fat[i + 1] << 4)) & 0x0FFF
    return (fat[i] | ((fat[i + 1] & 0x0F) << 8)) & 0x0FFF


def cluster_chain(fat: bytes, start_cluster: int) -> list[int]:
    chain: list[int] = []
    seen: set[int] = set()
    cluster = start_cluster
    while 2 <= cluster < 0xFF0 and cluster not in seen:
        seen.add(cluster)
        chain.append(cluster)
        cluster = fat12_entry(fat, cluster)
    return chain


def parse_root_directory(image: bytes) -> list[DirEntry]:
    entries: list[DirEntry] = []
    if len(image) < ROOT_OFFSET + ROOT_ENTRIES * 32:
        return entries
    for i in range(ROOT_ENTRIES):
        raw = image[ROOT_OFFSET + i * 32 : ROOT_OFFSET + (i + 1) * 32]
        if raw[0] == 0x00:
            break
        deleted = raw[0] == 0xE5
        attr = raw[11]
        name_bytes = raw[:8]
        ext_bytes = raw[8:11]
        if deleted:
            # 0xE5 is the FAT deletion marker; show as '?' in reports.
            name_bytes = b"?" + name_bytes[1:]
        name = name_bytes.decode("latin-1", "replace").rstrip()
        ext = ext_bytes.decode("latin-1", "replace").rstrip()
        start = struct.unpack_from("<H", raw, 26)[0]
        size = struct.unpack_from("<I", raw, 28)[0]
        entries.append(DirEntry(i, raw[:11], name, ext, attr, start, size, deleted))
    return entries


def extract_file_from_image(image: bytes, fat: bytes, entry: DirEntry) -> bytes:
    pieces: list[bytes] = []
    for cluster in cluster_chain(fat, entry.start_cluster):
        off = DATA_OFFSET + (cluster - 2) * CLUSTER_SIZE
        pieces.append(image[off : off + CLUSTER_SIZE])
    return b"".join(pieces)[: entry.size]


def looks_like_v50_disk_image(data: bytes) -> bool:
    """Heuristic for auto mode."""
    if len(data) < DATA_OFFSET + CLUSTER_SIZE:
        return False
    # The supplied V50 image is exactly 720 KiB, but do not require exact size.
    entries = parse_root_directory(data)
    plausible = 0
    for e in entries[:32]:
        if e.deleted:
            continue
        if e.size > 0 and e.start_cluster >= 2:
            plausible += 1
    return plausible >= 1 and len(data) >= 720 * 1024


# ---------------------------------------------------------------------------
# V50SEQV1 slot scanning
# ---------------------------------------------------------------------------


@dataclass
class V50TrackSlice:
    start: int
    end: int
    track_id: int
    raw: bytes

    @property
    def track_number(self) -> int:
        # In embedded V50 ALL slots the high nibble can identify the slot bank;
        # the low nibble is the musical/sequencer track number seen on this disk.
        return self.track_id & 0x0F


@dataclass
class V50SeqSlot:
    slot_index: int
    offset: int
    next_offset: int
    header: bytes
    song_name: str
    config: bytes
    routing: bytes
    flags: bytes
    tracks: list[V50TrackSlice]
    nseq_start: int
    nseq_end: int

    @property
    def raw_slot_len(self) -> int:
        return self.nseq_end - self.offset

    @property
    def raw_nseq_len(self) -> int:
        return self.nseq_end - self.nseq_start

    @property
    def tempo_bpm(self) -> int:
        return guess_tempo_bpm(self.header)


def guess_tempo_bpm(header: bytes, fallback: int = 120) -> int:
    """Guess V50 slot tempo from config bytes.

    In the supplied image, bytes header[10:12] behave like a 14-bit tempo value:
    00 78 -> 120, 00 7c -> 124, 01 0c -> 140, etc.
    """
    if len(header) >= 12:
        candidate = ((header[10] & 0x7F) << 7) | (header[11] & 0x7F)
        if 30 <= candidate <= 250:
            return candidate
    return fallback


def scan_v50_sequence_slots(filedata: bytes) -> Iterator[V50SeqSlot]:
    offsets = [m.start() for m in re.finditer(re.escape(V50SEQ_SIGNATURE), filedata)]
    for slot_index, off in enumerate(offsets):
        next_off = offsets[slot_index + 1] if slot_index + 1 < len(offsets) else len(filedata)
        header = filedata[off : off + V50SEQ_HEADER_LEN]
        if len(header) < V50SEQ_HEADER_LEN:
            continue
        song_name = latin1_clean(header[12:20]) or "NewSong"
        payload_start = off + V50SEQ_HEADER_LEN

        tracks: list[V50TrackSlice] = []
        pos = payload_start
        while pos + 2 <= next_off and filedata[pos] == 0xF0:
            end = filedata.find(b"\xF2", pos + 2, next_off)
            if end < 0:
                break
            raw = filedata[pos : end + 1]
            tracks.append(V50TrackSlice(pos, end + 1, filedata[pos + 1], raw))
            pos = end + 1

        yield V50SeqSlot(
            slot_index=slot_index,
            offset=off,
            next_offset=next_off,
            header=header,
            song_name=song_name,
            config=header[8:12],
            routing=header[20:28],
            flags=header[28:34],
            tracks=tracks,
            nseq_start=payload_start,
            nseq_end=tracks[-1].end if tracks else payload_start,
        )


def split_raw_nseq_tracks(nseq: bytes) -> list[V50TrackSlice]:
    """Split standalone raw NSEQ bytes into F0 id ... F2 tracks."""
    tracks: list[V50TrackSlice] = []
    pos = 0
    while pos + 2 <= len(nseq):
        if nseq[pos] != 0xF0:
            pos += 1
            continue
        end = nseq.find(b"\xF2", pos + 2)
        if end < 0:
            break
        raw = nseq[pos : end + 1]
        tracks.append(V50TrackSlice(pos, end + 1, nseq[pos + 1], raw))
        pos = end + 1
    return tracks


def channel_from_routing(routing: bytes, track_number: int) -> tuple[int, str, int | None]:
    """Return 1-based MIDI channel, reason string, and raw routing byte.

    V50 slot routing bytes in the supplied image are 00 01 02 ... 07.  Treat
    those as zero-based MIDI channels.  If a byte is missing or invalid, fall
    back to track number + 1 so the exported MIDI remains playable.
    """
    raw: int | None = None
    if 0 <= track_number < len(routing):
        raw = routing[track_number]
        if 0 <= raw <= 15:
            return raw + 1, "slot_routing_byte_zero_based", raw
    return (track_number % 16) + 1, "fallback_track_number_plus_one", raw


# ---------------------------------------------------------------------------
# NSEQ parser
# ---------------------------------------------------------------------------


@dataclass
class NSeqEvent:
    time_units: int
    order: int
    pos: int
    kind: str
    raw: bytes
    fields: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "time_units": self.time_units,
            "order": self.order,
            "pos": self.pos,
            "kind": self.kind,
            "raw_hex": hex_bytes(self.raw),
            **self.fields,
        }


@dataclass
class NSeqParseResult:
    track_id: int
    track_number: int
    events: list[NSeqEvent]
    warnings: list[str]
    counts: Counter
    final_time_units: int

    @property
    def unknown_events(self) -> list[NSeqEvent]:
        return [e for e in self.events if e.kind.startswith("unknown") or e.kind == "truncated"]


CONTROL_LENGTHS = {
    0xFA: 3,  # polyphonic aftertouch
    0xFB: 3,  # control change
    0xFC: 2,  # program change
    0xFD: 2,  # channel aftertouch
    0xFE: 3,  # pitch bend
}

CONTROL_NAMES = {
    0xFA: "poly_aftertouch",
    0xFB: "control_change",
    0xFC: "program_change",
    0xFD: "channel_aftertouch",
    0xFE: "pitch_bend",
}


def parse_time_bytes(time_bytes: Sequence[int]) -> int:
    # NSEQ long time is MS-byte then LS-byte, both 7-bit.  This also handles
    # rare runs longer than two bytes conservatively as a big-endian 7-bit VLQ.
    value = 0
    for b in time_bytes:
        value = (value << 7) | (b & 0x7F)
    return value


def parse_nseq_track(raw: bytes) -> NSeqParseResult:
    if len(raw) < 3 or raw[0] != 0xF0:
        raise ValueError("NSEQ track must begin with F0 <track-id> and end with/contain F2")

    track_id = raw[1]
    track_number = track_id & 0x0F
    end = len(raw) - 1 if raw[-1] == 0xF2 else len(raw)
    pos = 2
    time_units = 0
    order = 0
    events: list[NSeqEvent] = []
    warnings: list[str] = []
    counts: Counter = Counter()

    def add_event(kind: str, start: int, stop: int, **fields: Any) -> None:
        nonlocal order
        ev = NSeqEvent(time_units, order, start, kind, raw[start:stop], fields)
        events.append(ev)
        counts[kind] += 1
        order += 1

    while pos < end:
        time_start = pos
        time_bytes: list[int] = []
        while pos < end and raw[pos] < 0x80:
            time_bytes.append(raw[pos])
            pos += 1
        if time_bytes:
            delta = parse_time_bytes(time_bytes)
            time_units += delta
            counts["time_groups"] += 1
            counts["time_bytes"] += len(time_bytes)

        if pos >= end:
            break

        start = pos
        b = raw[pos]

        if b == 0xF2:
            break

        if b == 0xF5:
            add_event("measure_mark", start, start + 1)
            pos += 1
            continue

        if b == 0xF8:
            add_event("noop", start, start + 1)
            pos += 1
            continue

        if b in CONTROL_LENGTHS:
            length = CONTROL_LENGTHS[b]
            if pos + length > end:
                add_event("truncated", start, end, reason=f"truncated {CONTROL_NAMES[b]}")
                warnings.append(f"0x{start:x}: truncated {CONTROL_NAMES[b]}")
                break
            payload = raw[pos : pos + length]
            # All data bytes should be seven-bit.  Keep parsing even if odd.
            if any(x & 0x80 for x in payload[1:]):
                warnings.append(f"0x{start:x}: high bit set in {CONTROL_NAMES[b]} data bytes")
            if b == 0xFA:
                add_event("poly_aftertouch", start, pos + length, note=payload[1] & 0x7F, value=payload[2] & 0x7F)
            elif b == 0xFB:
                add_event("control_change", start, pos + length, controller=payload[1] & 0x7F, value=payload[2] & 0x7F)
            elif b == 0xFC:
                add_event("program_change", start, pos + length, program=payload[1] & 0x7F)
            elif b == 0xFD:
                add_event("channel_aftertouch", start, pos + length, value=payload[1] & 0x7F)
            elif b == 0xFE:
                # MIDI pitch bend is LSB then MSB.  NSEQ docs say same as MIDI
                # except for the status/MS byte, so preserve that order.
                lsb = payload[1] & 0x7F
                msb = payload[2] & 0x7F
                value14 = (msb << 7) | lsb
                add_event("pitch_bend", start, pos + length, lsb=lsb, msb=msb, value14=value14, signed=value14 - 8192)
            pos += length
            continue

        if 0x80 <= b <= 0xBF:
            # Short note:
            #   10dddddd 0kkkkkkk 0vvvvvvv
            #   10dddddd 1kkkkkkk             velocity defaults to 0x40
            if pos + 1 >= end:
                add_event("truncated", start, end, reason="truncated short note")
                warnings.append(f"0x{start:x}: truncated short note")
                break
            duration_units = b & 0x3F
            key_byte = raw[pos + 1]
            if key_byte & 0x80:
                note = key_byte & 0x7F
                velocity = 0x40
                add_event(
                    "note",
                    start,
                    pos + 2,
                    form="short_default_velocity",
                    note=note,
                    velocity=velocity,
                    duration_units=duration_units,
                )
                pos += 2
            else:
                if pos + 2 >= end:
                    add_event("truncated", start, end, reason="truncated short note velocity")
                    warnings.append(f"0x{start:x}: truncated short note velocity")
                    break
                note = key_byte & 0x7F
                velocity = raw[pos + 2] & 0x7F
                add_event(
                    "note",
                    start,
                    pos + 3,
                    form="short",
                    note=note,
                    velocity=velocity,
                    duration_units=duration_units,
                )
                pos += 3
            continue

        if 0xC0 <= b <= 0xFF:
            # Long note, after fixed F* controls above:
            #   11dddddd 0ddddddd 0kkkkkkk 0vvvvvvv
            #   110ddddd 0ddddddd 1kkkkkkk             velocity defaults to 0x40
            if pos + 2 >= end:
                add_event("truncated", start, end, reason="truncated long note")
                warnings.append(f"0x{start:x}: truncated long note")
                break
            d2 = raw[pos + 1]
            key_byte = raw[pos + 2]
            if d2 & 0x80:
                add_event("unknown", start, start + 1, reason="long note second duration byte has high bit set")
                warnings.append(f"0x{start:x}: long note second duration byte has high bit set")
                pos += 1
                continue
            if key_byte & 0x80:
                mask = 0x1F if b <= 0xDF else 0x3F
                duration_units = ((b & mask) << 7) | (d2 & 0x7F)
                note = key_byte & 0x7F
                velocity = 0x40
                add_event(
                    "note",
                    start,
                    pos + 3,
                    form="long_default_velocity",
                    note=note,
                    velocity=velocity,
                    duration_units=duration_units,
                )
                pos += 3
            else:
                if pos + 3 >= end:
                    add_event("truncated", start, end, reason="truncated long note velocity")
                    warnings.append(f"0x{start:x}: truncated long note velocity")
                    break
                duration_units = ((b & 0x3F) << 7) | (d2 & 0x7F)
                note = key_byte & 0x7F
                velocity = raw[pos + 3] & 0x7F
                add_event(
                    "note",
                    start,
                    pos + 4,
                    form="long",
                    note=note,
                    velocity=velocity,
                    duration_units=duration_units,
                )
                pos += 4
            continue

        add_event("unknown", start, start + 1, reason=f"unhandled byte 0x{b:02x}")
        warnings.append(f"0x{start:x}: unhandled byte 0x{b:02x}")
        pos += 1

    return NSeqParseResult(track_id, track_number, events, warnings, counts, time_units)


# ---------------------------------------------------------------------------
# Minimal Standard MIDI File writer
# ---------------------------------------------------------------------------


@dataclass(order=True)
class MidiEvent:
    tick: int
    order: int
    data: bytes = field(compare=False)
    description: str = field(default="", compare=False)


def vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("MIDI VLQ cannot encode negative values")
    buffer = value & 0x7F
    value >>= 7
    out = [buffer]
    while value:
        buffer = 0x80 | (value & 0x7F)
        out.append(buffer)
        value >>= 7
    return bytes(reversed(out))


def meta_event(meta_type: int, payload: bytes | str = b"") -> bytes:
    if isinstance(payload, str):
        payload = payload.encode("utf-8", "replace")
    return bytes([0xFF, meta_type]) + vlq(len(payload)) + payload


def tempo_meta_from_bpm(bpm: int) -> bytes:
    mpqn = int(round(60_000_000 / max(1, bpm)))
    return meta_event(0x51, mpqn.to_bytes(3, "big"))


def time_signature_meta(numerator: int = 4, denominator: int = 4) -> bytes:
    # MIDI stores denominator as power-of-two exponent.
    exponent = 0
    d = denominator
    while d > 1:
        d //= 2
        exponent += 1
    return meta_event(0x58, bytes([numerator & 0xFF, exponent & 0xFF, 24, 8]))


def track_chunk(events: list[MidiEvent]) -> bytes:
    events = sorted(events)
    body = bytearray()
    last_tick = 0
    for ev in events:
        if ev.tick < last_tick:
            raise ValueError("MIDI events are not sorted")
        body += vlq(ev.tick - last_tick)
        body += ev.data
        last_tick = ev.tick
    # Always terminate the track.  If the caller already supplied an EOT, this
    # adds a harmless extra zero-length EOT, but callers below do not supply one.
    body += vlq(0)
    body += meta_event(0x2F, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_smf(path: Path, tracks: list[list[MidiEvent]], ppq: int) -> None:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ppq)
    data = header + b"".join(track_chunk(t) for t in tracks)
    path.write_bytes(data)


def nseq_track_to_midi_events(
    parsed: NSeqParseResult,
    midi_channel: int,
    ppq: int,
    zero_duration_ticks: int,
    track_name: str,
    embed_meta: bool = True,
    emit_native_program_changes: bool = True,
    initial_program_info: dict[str, Any] | None = None,
) -> tuple[list[MidiEvent], dict[str, Any]]:
    ch = max(1, min(16, midi_channel)) - 1
    midi: list[MidiEvent] = []
    zero_duration_note_count = 0
    max_tick = 0

    midi.append(MidiEvent(0, -2000, meta_event(0x03, track_name), "track_name"))
    if embed_meta:
        midi.append(
            MidiEvent(
                0,
                -1999,
                meta_event(0x01, f"V50/NSEQ track id 0x{parsed.track_id:02X}; MIDI channel {midi_channel}"),
                "v50_track_text",
            )
        )

    if initial_program_info is not None:
        program = int(initial_program_info["midi_program_zero_based"]) & 0x7F
        midi.append(MidiEvent(0, -1980, bytes([0xC0 | ch, program]), "initial_gm_program_change"))
        if embed_meta:
            gm_text = f"GM fallback program: {program + 1} {GM_PROGRAM_NAMES[program]} ({initial_program_info.get('source', 'heuristic')})"
            midi.append(MidiEvent(0, -1979, meta_event(0x01, gm_text), "initial_gm_program_text"))

    for ev in parsed.events:
        tick = units_to_ticks(ev.time_units, NSEQ_TIME_UNITS_PER_QUARTER, ppq)
        max_tick = max(max_tick, tick)
        order = ev.order * 4

        if ev.kind == "note":
            note = int(ev.fields["note"]) & 0x7F
            velocity = int(ev.fields["velocity"]) & 0x7F
            duration_units = int(ev.fields["duration_units"])
            duration_ticks = units_to_ticks(duration_units, NSEQ_DURATION_UNITS_PER_QUARTER, ppq)
            if duration_ticks == 0:
                zero_duration_note_count += 1
                duration_ticks = max(0, zero_duration_ticks)
            off_tick = tick + duration_ticks
            max_tick = max(max_tick, off_tick)
            midi.append(MidiEvent(tick, order + 1, bytes([0x90 | ch, note, velocity]), "note_on"))
            midi.append(MidiEvent(off_tick, order + 2, bytes([0x80 | ch, note, 0]), "note_off"))

        elif ev.kind == "control_change":
            midi.append(
                MidiEvent(
                    tick,
                    order,
                    bytes([0xB0 | ch, int(ev.fields["controller"]) & 0x7F, int(ev.fields["value"]) & 0x7F]),
                    "control_change",
                )
            )

        elif ev.kind == "program_change":
            if emit_native_program_changes:
                midi.append(
                    MidiEvent(tick, order, bytes([0xC0 | ch, int(ev.fields["program"]) & 0x7F]), "program_change")
                )

        elif ev.kind == "channel_aftertouch":
            midi.append(
                MidiEvent(tick, order, bytes([0xD0 | ch, int(ev.fields["value"]) & 0x7F]), "channel_aftertouch")
            )

        elif ev.kind == "poly_aftertouch":
            midi.append(
                MidiEvent(
                    tick,
                    order,
                    bytes([0xA0 | ch, int(ev.fields["note"]) & 0x7F, int(ev.fields["value"]) & 0x7F]),
                    "poly_aftertouch",
                )
            )

        elif ev.kind == "pitch_bend":
            midi.append(
                MidiEvent(
                    tick,
                    order,
                    bytes([0xE0 | ch, int(ev.fields["lsb"]) & 0x7F, int(ev.fields["msb"]) & 0x7F]),
                    "pitch_bend",
                )
            )

        elif ev.kind == "measure_mark":
            # MIDI does not need a per-measure event.  Keep it in JSON/raw data.
            pass

        elif ev.kind == "noop":
            pass

        else:
            # Unknown/truncated events are preserved in JSON/raw data.  Do not
            # emit a fake MIDI event.
            pass

    summary = {
        "zero_duration_notes_seen": zero_duration_note_count,
        "zero_duration_ticks_used": zero_duration_ticks,
        "max_tick": max_tick,
        "native_program_changes_emitted": bool(emit_native_program_changes),
        "initial_gm_program": initial_program_info,
    }
    return midi, summary


# ---------------------------------------------------------------------------
# Output/report generation
# ---------------------------------------------------------------------------


@dataclass
class OutputPaths:
    root: Path
    root_files: Path
    midi: Path
    sidecars: Path
    raw_slots: Path
    raw_tracks: Path


def make_output_dirs(outdir: Path) -> OutputPaths:
    paths = OutputPaths(
        root=outdir,
        root_files=outdir / "root_files",
        midi=outdir / "midi",
        sidecars=outdir / "sidecars",
        raw_slots=outdir / "raw_slots",
        raw_tracks=outdir / "raw_tracks",
    )
    for p in (paths.root_files, paths.midi, paths.sidecars, paths.raw_slots, paths.raw_tracks):
        p.mkdir(parents=True, exist_ok=True)
    return paths


def program_change_groups(events: Iterable[NSeqEvent], ppq: int) -> list[dict[str, Any]]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for ev in events:
        if ev.kind == "program_change":
            grouped[ev.time_units].append(int(ev.fields["program"]))
    out = []
    for time_units in sorted(grouped):
        out.append(
            {
                "time_units": time_units,
                "time_ticks": units_to_ticks(time_units, NSEQ_TIME_UNITS_PER_QUARTER, ppq),
                "programs": grouped[time_units],
                "note": "same-time multiple program changes may represent Yamaha V50 memory/voice selection, not GM patches",
            }
        )
    return out


def event_count_dict(counts: Counter) -> dict[str, int]:
    return {k: int(v) for k, v in sorted(counts.items())}


def make_global_midi_track(song_name: str, source_label: str, tempo_bpm: int, embed_meta: bool = True) -> list[MidiEvent]:
    events = [
        MidiEvent(0, -3000, meta_event(0x03, song_name), "sequence_name"),
        MidiEvent(0, -2999, tempo_meta_from_bpm(tempo_bpm), "tempo"),
        MidiEvent(0, -2998, time_signature_meta(4, 4), "time_signature_default"),
    ]
    if embed_meta:
        events.append(MidiEvent(0, -2997, meta_event(0x01, f"Converted from Yamaha V50 NSEQ: {source_label}"), "source_text"))
        events.append(MidiEvent(0, -2996, meta_event(0x01, "V50 voice/performance data is preserved in sidecar/raw ALL file, not decoded into GM."), "v50_text"))
    return events


def convert_slot_to_outputs(
    *,
    source_file_label: str,
    source_file_data: bytes,
    source_file_path: Path | None,
    slot: V50SeqSlot,
    output_label: str,
    paths: OutputPaths,
    ppq: int,
    zero_duration_ticks: int,
    include_events_json: bool,
    write_midi: bool,
    write_raw: bool,
    embed_meta: bool,
    report_lines: list[str],
    program_mode: str = "gm-fallback",
    initial_program_overrides: dict[str, dict[int, int]] | None = None,
) -> None:
    song_safe = safe_name(slot.song_name, "song")
    label = f"{output_label}_slot{slot.slot_index}_{song_safe}"

    raw_slot_path: Path | None = None
    raw_nseq_path: Path | None = None
    if write_raw:
        raw_slot = source_file_data[slot.offset : slot.nseq_end]
        raw_nseq = source_file_data[slot.nseq_start : slot.nseq_end]
        raw_slot_path = paths.raw_slots / f"{label}.v50seqslot.bin"
        raw_nseq_path = paths.raw_slots / f"{label}.raw_nseq"
        raw_slot_path.write_bytes(raw_slot)
        raw_nseq_path.write_bytes(raw_nseq)

    tempo_bpm = slot.tempo_bpm
    all_midi_tracks: list[list[MidiEvent]] = [
        make_global_midi_track(slot.song_name, f"{source_file_label} slot {slot.slot_index}", tempo_bpm, embed_meta=embed_meta)
    ]
    sidecar_tracks: list[dict[str, Any]] = []
    total_counts: Counter = Counter()
    all_warnings: list[str] = []
    initial_program_overrides = initial_program_overrides or {"track": {}, "channel": {}}
    v50_name_table = extract_v50_primary_name_table(source_file_data)
    v50_name_shift = choose_v50_name_shift(v50_name_table)
    emit_native_program_changes = program_mode in {"preserve", "gm-fallback"}

    for track_index, tr in enumerate(slot.tracks):
        parsed = parse_nseq_track(tr.raw)
        total_counts.update(parsed.counts)
        all_warnings.extend(parsed.warnings)
        midi_channel, channel_reason, raw_routing_byte = channel_from_routing(slot.routing, parsed.track_number)
        track_name = f"{slot.song_name} tr{parsed.track_number + 1} ch{midi_channel}"
        initial_program_info = choose_initial_program_info(
            parsed=parsed,
            midi_channel=midi_channel,
            program_mode=program_mode,
            overrides=initial_program_overrides,
            v50_name_table=v50_name_table,
            v50_name_shift=v50_name_shift,
        )
        track_midi, midi_summary = nseq_track_to_midi_events(
            parsed,
            midi_channel=midi_channel,
            ppq=ppq,
            zero_duration_ticks=zero_duration_ticks,
            track_name=track_name,
            embed_meta=embed_meta,
            emit_native_program_changes=emit_native_program_changes,
            initial_program_info=initial_program_info,
        )
        all_midi_tracks.append(track_midi)

        raw_track_path: Path | None = None
        if write_raw:
            raw_track_path = paths.raw_tracks / f"{label}_track{track_index}_id{tr.track_id:02x}.raw_nseq_track"
            raw_track_path.write_bytes(tr.raw)

        unknowns = [e.to_json() for e in parsed.unknown_events]
        sidecar_track: dict[str, Any] = {
            "track_index_in_slot": track_index,
            "track_id_hex": f"0x{tr.track_id:02X}",
            "track_number_zero_based": parsed.track_number,
            "track_number_one_based": parsed.track_number + 1,
            "source_offset_hex": f"0x{tr.start:X}",
            "source_length_bytes": len(tr.raw),
            "raw_sha256": sha256_hex(tr.raw),
            "raw_track_file": relpath(raw_track_path, paths.root) if raw_track_path else None,
            "midi_channel_one_based": midi_channel,
            "channel_assignment_reason": channel_reason,
            "raw_routing_byte": raw_routing_byte,
            "event_counts": event_count_dict(parsed.counts),
            "final_time_units": parsed.final_time_units,
            "final_time_ticks": units_to_ticks(parsed.final_time_units, NSEQ_TIME_UNITS_PER_QUARTER, ppq),
            "midi_summary": midi_summary,
            "program_change_groups": program_change_groups(parsed.events, ppq),
            "initial_gm_program": initial_program_info,
            "warnings": parsed.warnings,
            "unknown_events": unknowns,
        }
        if include_events_json:
            sidecar_track["decoded_events"] = [e.to_json() for e in parsed.events]
        sidecar_tracks.append(sidecar_track)

    midi_path: Path | None = None
    if write_midi:
        midi_path = paths.midi / f"{label}.mid"
        write_smf(midi_path, all_midi_tracks, ppq=ppq)

    routing_map = []
    for i in range(8):
        channel, reason, raw_byte = channel_from_routing(slot.routing, i)
        routing_map.append(
            {
                "track_number_zero_based": i,
                "track_number_one_based": i + 1,
                "raw_routing_byte": raw_byte,
                "midi_channel_one_based": channel,
                "reason": reason,
            }
        )

    sidecar = {
        "format": "yamaha_v50_nseq_conversion_sidecar",
        "format_version": 1,
        "source": {
            "source_file_label": source_file_label,
            "source_file_path": str(source_file_path) if source_file_path else None,
            "source_file_sha256": sha256_hex(source_file_data),
            "source_file_size_bytes": len(source_file_data),
        },
        "slot": {
            "slot_index": slot.slot_index,
            "slot_offset_hex": f"0x{slot.offset:X}",
            "slot_next_offset_hex": f"0x{slot.next_offset:X}",
            "signature": latin1_clean(slot.header[:8]),
            "header_hex": hex_bytes(slot.header),
            "config_hex": hex_bytes(slot.config),
            "song_name": slot.song_name,
            "routing_hex": hex_bytes(slot.routing),
            "routing_map": routing_map,
            "flags_hex": hex_bytes(slot.flags),
            "tempo_bpm": tempo_bpm,
            "tempo_source": "14-bit value stored in V50SEQV1 header bytes 10 and 11 when in plausible BPM range",
            "raw_slot_file": relpath(raw_slot_path, paths.root) if raw_slot_path else None,
            "raw_nseq_file": relpath(raw_nseq_path, paths.root) if raw_nseq_path else None,
            "raw_slot_sha256": sha256_hex(source_file_data[slot.offset : slot.nseq_end]),
            "raw_nseq_sha256": sha256_hex(source_file_data[slot.nseq_start : slot.nseq_end]),
        },
        "conversion": {
            "midi_file": relpath(midi_path, paths.root) if midi_path else None,
            "smf_format": 1,
            "ppq": ppq,
            "nseq_time_units_per_quarter": NSEQ_TIME_UNITS_PER_QUARTER,
            "nseq_duration_units_per_quarter": NSEQ_DURATION_UNITS_PER_QUARTER,
            "zero_duration_ticks": zero_duration_ticks,
            "program_mode": program_mode,
            "program_change_policy": "preserve native NSEQ program changes unless program_mode is gm or none; optionally add GM fallback program changes so DAWs do not default every track to Acoustic Grand Piano",
            "v50_name_table_policy": "0x0440 name table is a best-effort heuristic for GM fallback only; exact V50 voices/performances remain in the raw ALL file",
            "v50_name_lookup_shift": v50_name_shift,
            "v50_primary_name_table": v50_name_table,
            "voice_performance_policy": "not fully decoded; preserve the containing ALL/root file and raw slot data for future Yamaha V50 voice/performance decoding",
            "event_counts_total": event_count_dict(total_counts),
            "warnings": all_warnings,
        },
        "tracks": sidecar_tracks,
    }

    sidecar_path = paths.sidecars / f"{label}.v50.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    report_lines.append(
        f"    slot {slot.slot_index}: {slot.song_name!r}, tempo={tempo_bpm}, "
        f"tracks={len(slot.tracks)}, midi={relpath(midi_path, paths.root) if midi_path else '(not written)'}, "
        f"sidecar={relpath(sidecar_path, paths.root)}"
    )
    for tr in sidecar_tracks:
        prog = tr.get("initial_gm_program")
        prog_text = ""
        if prog:
            prog_text = f", gm={prog['midi_program_one_based']}:{prog['gm_name']}"
        report_lines.append(
            f"      track {tr['track_index_in_slot']}: id={tr['track_id_hex']}, "
            f"track={tr['track_number_one_based']}, ch={tr['midi_channel_one_based']}" + prog_text + ", "
            f"events={tr['event_counts']}"
        )


def process_v50_all_file(
    *,
    filedata: bytes,
    source_file_label: str,
    output_label: str,
    paths: OutputPaths,
    args: argparse.Namespace,
    report_lines: list[str],
    source_file_path: Path | None = None,
) -> int:
    slots = list(scan_v50_sequence_slots(filedata))
    if not slots:
        return 0

    report_lines.append(f"  {source_file_label}: V50SEQV1 slots={len(slots)}")
    converted = 0
    for slot in slots:
        if not slot.tracks:
            report_lines.append(f"    slot {slot.slot_index}: {slot.song_name!r}, empty")
            continue
        convert_slot_to_outputs(
            source_file_label=source_file_label,
            source_file_data=filedata,
            source_file_path=source_file_path,
            slot=slot,
            output_label=output_label,
            paths=paths,
            ppq=args.ppq,
            zero_duration_ticks=args.zero_duration_ticks,
            include_events_json=args.include_events_json,
            write_midi=not args.no_midi,
            write_raw=not args.no_raw,
            embed_meta=not args.no_embed_meta,
            report_lines=report_lines,
            program_mode=args.program_mode,
            initial_program_overrides=args.initial_program_overrides,
        )
        converted += 1
    return converted


def process_raw_nseq(
    *,
    nseq: bytes,
    source_label: str,
    output_label: str,
    paths: OutputPaths,
    args: argparse.Namespace,
    report_lines: list[str],
) -> int:
    tracks = split_raw_nseq_tracks(nseq)
    if not tracks:
        report_lines.append(f"No F0 ... F2 NSEQ tracks found in {source_label}")
        return 0

    # Create a synthetic slot-like object so raw NSEQ files use the same output path.
    tempo_hi = (args.fallback_tempo >> 7) & 0x7F
    tempo_lo = args.fallback_tempo & 0x7F
    header = V50SEQ_SIGNATURE + bytes([0, 0, tempo_hi, tempo_lo]) + b"RawNSEQ " + bytes(range(8)) + b"\xff" * 6
    slot = V50SeqSlot(
        slot_index=0,
        offset=0,
        next_offset=len(nseq),
        header=header[:V50SEQ_HEADER_LEN],
        song_name=safe_name(Path(source_label).stem, "RawNSEQ")[:8],
        config=header[8:12],
        routing=bytes(range(8)),
        flags=b"\xff" * 6,
        tracks=tracks,
        nseq_start=0,
        nseq_end=len(nseq),
    )
    convert_slot_to_outputs(
        source_file_label=source_label,
        source_file_data=nseq,
        source_file_path=None,
        slot=slot,
        output_label=output_label,
        paths=paths,
        ppq=args.ppq,
        zero_duration_ticks=args.zero_duration_ticks,
        include_events_json=args.include_events_json,
        write_midi=not args.no_midi,
        write_raw=not args.no_raw,
        embed_meta=not args.no_embed_meta,
        report_lines=report_lines,
        program_mode=args.program_mode,
        initial_program_overrides=args.initial_program_overrides,
    )
    return 1


def process_disk_image(image_path: Path, image: bytes, paths: OutputPaths, args: argparse.Namespace, report_lines: list[str]) -> int:
    fat = image[FAT_OFFSET : FAT_OFFSET + SECTORS_PER_FAT * BYTES_PER_SECTOR]
    entries = parse_root_directory(image)

    report_lines.append(f"Input image: {image_path}")
    report_lines.append(f"Image size: {len(image)} bytes")
    report_lines.append("Inferred disk layout:")
    report_lines.append(f"  FAT offset:      0x{FAT_OFFSET:04X}")
    report_lines.append(f"  root dir offset: 0x{ROOT_OFFSET:04X}")
    report_lines.append(f"  data offset:     0x{DATA_OFFSET:04X}")
    report_lines.append(f"  cluster size:    {CLUSTER_SIZE} bytes")
    report_lines.append("")
    report_lines.append("Root directory:")

    converted_slots = 0
    for entry in entries:
        status = "deleted" if entry.deleted else "active"
        chain = cluster_chain(fat, entry.start_cluster)
        report_lines.append(
            f"  #{entry.index:02d} {entry.display_name:<12} {status:<7} "
            f"start={entry.start_cluster:<3} size={entry.size:<6} clusters={len(chain)}"
        )
        if entry.deleted:
            continue
        # Skip obvious volume-label and subdirectory entries, but keep odd Yamaha filenames.
        if entry.attr & 0x08 or entry.attr & 0x10:
            report_lines.append("    skipped volume-label/subdirectory entry")
            continue

        filedata = extract_file_from_image(image, fat, entry)
        root_path = paths.root_files / entry.safe_filename
        root_path.write_bytes(filedata)

        if V50SEQ_SIGNATURE in filedata:
            converted_slots += process_v50_all_file(
                filedata=filedata,
                source_file_label=entry.display_name,
                output_label=Path(entry.safe_filename).stem,
                paths=paths,
                args=args,
                report_lines=report_lines,
                source_file_path=root_path,
            )

    return converted_slots


def process_input(input_path: Path, outdir: Path, args: argparse.Namespace) -> None:
    paths = make_output_dirs(outdir)
    data = input_path.read_bytes()
    mode = args.mode
    if mode == "auto":
        if looks_like_v50_disk_image(data):
            mode = "image"
        elif V50SEQ_SIGNATURE in data:
            mode = "file"
        elif data.startswith(b"\xF0") or b"\xF0" in data[:32]:
            mode = "raw-nseq"
        else:
            raise SystemExit("Could not auto-detect input. Use --mode image, --mode file, or --mode raw-nseq.")

    report_lines: list[str] = []
    report_lines.append("Yamaha V50/NSEQ conversion report")
    report_lines.append("==================================")
    report_lines.append(f"Input: {input_path}")
    report_lines.append(f"Mode: {mode}")
    report_lines.append(f"PPQ: {args.ppq}")
    report_lines.append(f"Program mode: {args.program_mode}")
    if args.initial_programs:
        report_lines.append(f"Initial program overrides: {args.initial_programs}")
    report_lines.append("")

    if mode == "image":
        converted = process_disk_image(input_path, data, paths, args, report_lines)
    elif mode == "file":
        copied = paths.root_files / safe_name(input_path.name, "input_file")
        copied.write_bytes(data)
        converted = process_v50_all_file(
            filedata=data,
            source_file_label=input_path.name,
            output_label=safe_name(input_path.stem, "input"),
            paths=paths,
            args=args,
            report_lines=report_lines,
            source_file_path=copied,
        )
    elif mode == "raw-nseq":
        converted = process_raw_nseq(
            nseq=data,
            source_label=input_path.name,
            output_label=safe_name(input_path.stem, "raw_nseq"),
            paths=paths,
            args=args,
            report_lines=report_lines,
        )
    else:
        raise SystemExit(f"Unsupported mode: {mode}")

    report_lines.append("")
    report_lines.append(f"Converted non-empty songs: {converted}")
    report_lines.append(f"Output directory: {outdir}")
    (outdir / "conversion_report.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Converted non-empty songs: {converted}")
    print(f"Wrote: {outdir}")
    print(f"Report: {outdir / 'conversion_report.txt'}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract Yamaha V50 V50SEQV1/NSEQ songs and convert them to Standard MIDI Files with preservation sidecars."
    )
    p.add_argument("input", type=Path, help="V50 720K disk image, extracted V50 ALL file, or raw NSEQ file")
    p.add_argument("outdir", type=Path, help="Directory to write root files, raw NSEQ, MIDI, JSON sidecars, and report")
    p.add_argument("--mode", choices=["auto", "image", "file", "raw-nseq"], default="auto", help="Input type; default auto")
    p.add_argument("--ppq", type=int, default=96, help="MIDI pulses per quarter note. Default 96 maps NSEQ delta units exactly.")
    p.add_argument(
        "--zero-duration-ticks",
        type=int,
        default=1,
        help="MIDI duration to use for NSEQ notes whose decoded duration is zero. Use 0 for literal zero-length notes. Default 1.",
    )
    p.add_argument(
        "--include-events-json",
        action="store_true",
        help="Include every decoded NSEQ event in sidecar JSON. This can make JSON files much larger.",
    )
    p.add_argument("--no-midi", action="store_true", help="Do not write .mid files")
    p.add_argument("--no-raw", action="store_true", help="Do not write raw slot/NSEQ/track files")
    p.add_argument("--no-embed-meta", action="store_true", help="Do not add explanatory text meta events to MIDI files")
    p.add_argument(
        "--program-mode",
        choices=["gm-fallback", "preserve", "gm", "none"],
        default="gm-fallback",
        help=(
            "Program-change handling. gm-fallback preserves native NSEQ program changes and adds a GM fallback only "
            "when a note track has none, so DAWs do not default to Acoustic Grand Piano. preserve matches the older "
            "behavior. gm suppresses native Yamaha/V50 program changes and emits only GM guesses/overrides. none emits "
            "no program changes. Default gm-fallback."
        ),
    )
    p.add_argument(
        "--initial-programs",
        default="",
        help=(
            "Optional GM fallback overrides, e.g. 'track1=1,ch2=33,3=strings'. Bare numbers before '=' apply "
            "to both one-based track and channel numbers. Numeric program values are GM 1-128; 0 is also accepted "
            "as Acoustic Grand Piano. Names such as piano, bass, strings, brass, lead, pad are accepted."
        ),
    )
    p.add_argument("--fallback-tempo", type=int, default=120, help="Fallback tempo for raw NSEQ mode")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.ppq <= 0:
        raise SystemExit("--ppq must be positive")
    if args.zero_duration_ticks < 0:
        raise SystemExit("--zero-duration-ticks must be >= 0")
    try:
        args.initial_program_overrides = parse_initial_program_overrides(args.initial_programs)
    except ValueError as exc:
        raise SystemExit(f"--initial-programs: {exc}") from exc
    args.outdir.mkdir(parents=True, exist_ok=True)
    process_input(args.input, args.outdir, args)


if __name__ == "__main__":
    main()
