
# Yamaha E-SEQ and PIANODIR.FIL Reference

**Prepared for:** APS MIDI Prep Tool  
**Purpose:** implementation reference for converting Yamaha E-SEQ `.FIL` song files to Standard MIDI Files, converting Standard MIDI Files back to E-SEQ `.FIL`, and constructing `PIANODIR.FIL` disk indexes for older Yamaha Disklavier media.  
**Revision:** 1.0-precorpus  
**Date:** 2026-04-25

> This document is intentionally written as an engineering reference. It distinguishes proven behavior from inferred or compatibility-oriented behavior. It does not contain proprietary Yamaha source code or third-party program code; it specifies file behavior derived from personal recordings, supplied disk images, static binary inspection, and public format references.

---

## 1. Executive summary

E-SEQ is a Yamaha song-file format used by older Disklavier and related Yamaha player systems. On old E-SEQ floppy disks, the individual songs are usually stored as `.FIL` files and the disk-level song list is stored in `PIANODIR.FIL`.

The supported conversion model is:

```text
E-SEQ .FIL song file  <---->  Standard MIDI File, preferably SMF type 0
       ^
       |
       +---- PIANODIR.FIL indexes .FIL songs for old Disklavier media
```

The most important implementation findings are:

1. The common user-recording `.FIL` variant begins with `FE 00 00`, contains `COM-ESEQ` at file offset `0x07`, and begins its event stream at offset `0x77`.
2. The event stream contains MIDI channel messages almost directly. Delay opcodes (`F3`, `F4`) advance an absolute tick counter; channel messages are emitted at that tick.
3. E-SEQ-to-MIDI conversion writes Standard MIDI type 0 with one track and `384` ticks per quarter note in the tested converter style.
4. Initial tempo for the tested normal variant is not inferred from note spacing. It is:

   ```text
   base_bpm = eseq[0x33] + 29
   midi_mpqn = floor(60000000 / base_bpm)
   ```

5. In-stream E-SEQ opcode `FB lo hi` means a tempo factor:

   ```text
   factor = (hi << 7) | (lo & 0x7F)
   effective_bpm = base_bpm * factor / 1000
   midi_mpqn = floor(60000000 / effective_bpm)
   ```

6. `PIANODIR.FIL` is not musical event data. It is the disk index: song order, active-song list, display titles, durations, and copied E-SEQ header metadata.
7. For normal `.FIL` songs in the analyzed disk-image corpus, each `PIANODIR.FIL` song record is essentially `eseq_file[0x27:0x77]` with the first eleven bytes replaced by the actual DOS 8.3 filename. If the event stream has been delay-edited, refresh the duration and before/after delay words from the stream; copied header bytes can be stale.
8. A second E-SEQ header variant, marked `Q11V1.00`, exists. Its event stream begins at `0x0200`, and its `PIANODIR.FIL` record must be built by a special recipe.
9. Early MIDI `CC7 Channel Volume = 0` events on piano channels are significant. In generic MIDI playback they mute the channel, but in Yamaha/Disklavier workflows they may be intentional playback-control or compatibility behavior. APS MIDI Prep Tool should detect them and offer explicit handling policies.

---

## 2. Evidence and confidence labels

### 2.1 Evidence base

The specification below is based on:

- 21 matched E-SEQ/MIDI pairs of personal recordings.
- Static inspection of a 1998-era `ESEQ2MID.EXE` that converts E-SEQ to MIDI type 0.
- 110 Yamaha disk images containing `PIANODIR.FIL` and E-SEQ files.
- Static inspection of a third-party `EEXPLORE.EXE` index utility; it was not executed and is treated as secondary evidence.
- Public references for Standard MIDI Files, MIDI control-change numbering, Disklavier E-SEQ disk conventions, and public Disklavier disk-image tools.

### 2.2 Corpus summary for `PIANODIR.FIL`

From the 110 supplied disk images:

| Observation | Count |
|---|---:|
| Supplied disk images | 110 |
| Images with a root-directory `PIANODIR.FIL` entry | 108 |
| Images with valid `PIANODIR.FIL` header | 107 |
| Valid indexes of size `0x1800` / 6144 bytes | 91 |
| Valid indexes of size `0x1400` / 5120 bytes | 16 |
| Parsed active song records | 1,239 |
| Normal E-SEQ records | 1,211 |
| `Q11V1.00` records | 28 |
| Normal records exactly reproduced by the header-slice rule | 1,116 |
| Normal records differing only at record byte `0x2A` | 95 |

One image, `CSC1045.img`, had a `PIANODIR.FIL` root entry whose first data sector contained the literal bad-sector filler pattern `-=[BAD SECTOR]=-`; treat its index as corrupt. Two images, `AC-56511-2.img` and `PSP3303.img`, did not contain a usable root-directory `PIANODIR.FIL` entry.

### 2.3 Confidence labels used in this document

| Label | Meaning | Example |
|---|---|---|
| **Proven** | Confirmed by matched pairs, static converter behavior, and/or full disk-image corpus | `F3`/`F4` delays, normal `PIANODIR` record rule, `base_bpm = byte + 29` for the tested normal variant, duration/before/after delay fields, arrangement/type display code, write-protect flag |
| **Strongly inferred** | Consistent across available samples and tooling, but may not cover every Yamaha variant | `F9 04 02` as a 4/4 bar marker |
| **Compatibility rule** | Recommended writer behavior because it matches known converters or old media conventions | Uppercase DOS 8.3 filenames, 6144-byte `PIANODIR.FIL` output |
| **Open** | Field or behavior not fully explained; copy/preserve rather than interpret | Several opaque header bytes and `PIANODIR` record byte `0x2A` |

---

## 3. Terms and file roles

| Term | Meaning |
|---|---|
| E-SEQ | Yamaha sequence format used by older Disklavier and related Yamaha systems. |
| `.FIL` | Common extension for one E-SEQ song file on old Disklavier media. Some disks also index files with extensions like `.P01`, `.P02`, etc. |
| Standard MIDI File / SMF | Standard `.mid` container with `MThd` and `MTrk` chunks. |
| SMF type 0 | Single-track Standard MIDI File. Historical E-SEQ converters commonly produce this. |
| `PIANODIR.FIL` | Disk index for old E-SEQ media. It lists active E-SEQ files, order, titles, duration fields, and metadata copied from song headers. |
| Normal E-SEQ variant | The common `COM-ESEQ` variant whose event stream starts at `0x77`. |
| `Q11V1.00` variant | E-SEQ header variant observed in the disk-image corpus. Event stream starts at `0x0200`, and `PIANODIR` records require a special recipe. |

Public Yamaha material describes E-SEQ as a Yamaha format compatible with Disklavier, Clavinova, and other Yamaha products. Public DKVUTILS-era descriptions say old Disklavier E-SEQ disks use `.FIL` music files plus a `PIANODIR.FIL` index that lists active files, titles, and file positions, and that the index is normally 6 KB.

---

## 4. Standard MIDI reference model

### 4.1 Required MIDI file model

APS MIDI Prep Tool should support reading SMF type 0 and type 1. For writing E-SEQ-derived MIDI, the reference output should be:

| MIDI property | Recommended value |
|---|---|
| SMF format | `0` |
| Track count | `1` |
| Division | `384` ticks per quarter note (`0x0180`) |
| Tempo representation | `FF 51 03 tt tt tt`, microseconds per quarter note |
| End of track | `FF 2F 00` |

The Standard MIDI File specification defines the set-tempo event `FF 51 03` as microseconds per MIDI quarter note and defines `FF 2F 00` as the required end-of-track marker. It also defines `FF 58` time signature, where the denominator byte is a power-of-two exponent.

### 4.2 Tempo math

MIDI tempo is stored as integer microseconds per quarter note (`MPQN`), not BPM.

```text
mpqn = floor(60000000 / bpm)
bpm  = 60000000 / mpqn
```

Example:

```text
117 BPM -> floor(60000000 / 117) = 512820 = 0x07D334
MIDI event: 00 FF 51 03 07 D3 34
```

Because `MPQN` is an integer, a DAW may display a value such as `117.000117 BPM` instead of exactly `117`.

### 4.3 MIDI control-change facts relevant to Disklavier conversion

MIDI channel control-change messages have status bytes `0xB0..0xBF`, followed by controller number and value. MIDI controller 7 is Channel Volume, values `0..127`; MIDI controller 64 is Damper/Sustain Pedal, with values `0..63` off and `64..127` on under the standard MIDI convention.

This matters because Disklavier piano playback often uses dense note and pedal data, and because un-restored `CC7 = 0` can make a generic MIDI synthesizer produce no audible piano despite valid note events.

---

## 5. Normal E-SEQ `.FIL` file structure

### 5.1 Top-level layout

A normal E-SEQ `.FIL` file in the analyzed converter/personal-recording style has:

```text
Offset range        Meaning
0x0000..0x0076      Header and display metadata
0x0077..F2          Event stream
after F2            Padding or slack, often to a block boundary
```

The first bytes are:

```text
FE 00 00 <length bytes> 43 4F 4D 2D 45 53 45 51 ...
```

`43 4F 4D 2D 45 53 45 51` is ASCII `COM-ESEQ` and is normally located at file offset `0x07`.

### 5.2 Header fields used by conversion software

The following table is written for implementation. Some fields are fully interpreted; opaque fields should be copied from a known-good template or from the source file during edits.

| File offset | Size | Meaning | Reader behavior | Writer behavior |
|---:|---:|---|---|---|
| `0x00` | 1 | Start marker | Expect `FE` for normal files. | Write `FE`. |
| `0x03` | 4 | Length/control field | In the 21 matched personal files, equals `F2_offset + 1`. In factory-image files, this field can differ; do not rely on it alone. | For generated converter-style files, write `F2_offset + 1`. |
| `0x07` | 8 | Signature | Expect `COM-ESEQ`. | Write `COM-ESEQ`. |
| `0x0F` | 8 | Variant/version area | Blank/zero in the 21 personal files; factory files may contain values such as `714003`, `714007 C`; Q11 files contain `Q11V1.00`. | Preserve or write a known-good normal variant template. |
| `0x17..0x1E` | 8 | Opaque normal-header constants/flags | Preserve. | Copy from template. |
| `0x1F` | 4 | Event-stream length | Often equals `F2_offset + 1 - 0x77` for normal files; more reliable than `0x03` in the disk corpus. | Write `F2_offset + 1 - 0x77`. |
| `0x23` | 1 | Constant/flag | Often `01` in generated personal files. | Copy template or set `01`. |
| `0x24` | 1 | Tempo mirror / variant tempo byte | Mirrors `0x33` in the tested normal personal files; used as tempo byte in Q11-style `PIANODIR` records. | For normal generated files, mirror `0x33`. |
| `0x27..0x31` | 11 | DOS 8.3 filename bytes without dot | Used by `PIANODIR` construction. | Write uppercase 8-byte name + 3-byte extension, space padded. |
| `0x33` | 1 | Initial tempo byte for normal variant | `base_bpm = byte + 29`. | `tempo_byte = clamp(round(base_bpm) - 29, 0, 255)`. |
| `0x34..0x35` | 2 | Time-signature display | Personal files use `04 04`; likely natural numerator/denominator. | For 4/4 write `04 04`; other meters are provisional. |
| `0x37..0x3A` | 4 | Duration / end-tick accumulator | Little-endian. `EEXPLORE` displays this as seconds by dividing by `750`. In generated personal files it equals the cumulative E-SEQ tick at `F2`. | For generated files, write selected E-SEQ end tick. |
| `0x3B..0x3C` | 2 | Delay before first note | Little-endian E-SEQ ticks. `EEXPLORE` displays milliseconds as `ticks * 1000 / 750`. Setup/controller events may occur earlier and should not collapse this value. | Recalculate from the event stream. |
| `0x3D..0x3E` | 2 | Per-song secondary word | Summed into the disk-info secondary aggregate in many indexes. Exact semantics still open. | Copy/preserve. |
| `0x3F..0x40` | 2 | Delay after last real event | Little-endian E-SEQ ticks from last real event to `F2`/end tick. Display conversion matches delay-before. | Recalculate from the event stream. |
| `0x41..0x42` | 2 | Opaque playback/index metadata | Copy/preserve. | Copy from template/source. |
| `0x43..0x46` | 4 | Event-start helper bytes | Normal files show `00 77 00 00`; Q11 `PIANODIR` records show `02 00 00 00`. | For normal generated files write `00 77 00 00`. |
| `0x47..0x4E` | 8 | Opaque constants/metadata | Personal files often show `10 7F 00 00 41 xx 00 00`. | Copy from template/source. |
| `0x4F` | 1 | Write-protect flag | Bit `0x80` set means write-protected. `0x00` means write-protect off in tested samples. Mirrors `PIANODIR` record byte `0x28`. | Preserve from source, or write `0x80` for generated default write-protected files. |
| `0x50` | 1 | Arrangement/type display code | Low two bits match `PIANODIR` record byte `0x29`: `0` Solo, `1` L-R Split, `2` Ensemble. | Preserve from source, or write `0` for generated default Solo. |
| `0x51` | 1 | Controller/pedal-present flag | Often `1` when controller events are present. | Set consistently with event stream or copy template. |
| `0x53` | 1 | Opaque constant | Often `41`. | Copy template. |
| `0x54` | 1 | Event-class bitmask | Personal files: `0x01` notes, `0x04` controllers, `0x05` both. | Set `0x01` if notes, `0x04` if controllers, OR both. |
| `0x56` | 1 | Opaque source/category flag | Varied in personal files. | Copy template unless corpus establishes meaning. |
| `0x57..0x76` | 32 | Song title/display field | Used by converters as track name or title. | Write title as single-byte text, padded with spaces or NULs. |
| `0x77` | variable | Event stream start for normal variant | Parse from here unless variant detection says otherwise. | Begin event stream here. |

### 5.3 Important length-field warning

Do not parse normal E-SEQ files by trusting only the 32-bit field at `0x03`. In the 21 personal recording files, `0x03..0x06` equals `F2_offset + 1`. In the larger disk-image corpus, many factory files have a different value while the stream-length field at `0x1F` and the actual `F2` marker remain usable.

Recommended reader policy:

1. Locate and verify `COM-ESEQ`.
2. Detect known variant.
3. Choose event start (`0x77` for normal, `0x0200` for Q11).
4. Parse until opcode `F2`, with the file size and stream-length field as safety bounds.
5. Treat `0x03` as metadata, not as the only authoritative EOF.

### 5.4 Padding and slack

The 21 personal/converter-style `.FIL` files were padded to 2048-byte boundaries with `F6`. Factory disk-image `.FIL` files often contain zero slack after `F2`. A writer should offer:

| Padding mode | Behavior |
|---|---|
| `compat_f6` | Pad to a 2048-byte boundary with `F6`; best for generated old-hardware files. |
| `zero` | Pad with zero bytes; useful for matching some factory disk-image styles. |
| `compact` | Stop at `F2`; useful for internal tests, not recommended for old physical media. |
| `preserve` | Preserve source-file slack when editing. |

---

## 6. Q11V1.00 E-SEQ variant

The disk-image corpus contains a variant with:

```text
COM-ESEQ at offset 0x07
Q11V1.00 at offset 0x0F
Event stream begins at 0x0200
```

For this variant:

- Do not use `0x77` as the event-stream start.
- The normal header slice `file[0x27:0x77]` is mostly blank and is not a usable `PIANODIR` record.
- The `PIANODIR` record must be constructed with the Q11 recipe in section 13.5.
- The PIANODIR tempo byte comes from `file[0x24]`, not `file[0x33]`.

E-SEQ-to-MIDI conversion should still parse the event stream with the same opcode grammar once the correct start offset is known, but Q11 files deserve separate regression tests.

---

## 7. E-SEQ event-stream grammar

### 7.1 Parser model

The E-SEQ event stream is a byte stream. The parser maintains an absolute tick counter.

```text
current_tick = 0
read opcode
if opcode is delay: current_tick += delay
if opcode is MIDI channel event: emit event at current_tick
if opcode is tempo/bar/meta marker: process at current_tick
if opcode is F2: stop
```

For conversion to Standard MIDI, one E-SEQ event tick maps to one MIDI tick in a `384` PPQN MIDI file. The tempo event determines real-time playback.

### 7.2 Opcode table

| Opcode/range | Length | Meaning | MIDI conversion |
|---|---:|---|---|
| `0x00..0x7F` outside known payload | 1 | Filler or sub-byte | Usually skip with diagnostics if unexpected. |
| `0x80..0x8F` | 3 | MIDI Note Off | Copy status and two data bytes. |
| `0x90..0x9F` | 3 | MIDI Note On | Copy status and two data bytes. Velocity `0` is note-off by MIDI convention. |
| `0xA0..0xAF` | 3 | MIDI Polyphonic Key Pressure | Copy. |
| `0xB0..0xBF` | 3 | MIDI Control Change | Copy, except optional policies for un-restored CC7=0 before later notes. |
| `0xC0..0xCF` | 2 | MIDI Program Change | Copy or optionally insert/remove in compatibility modes. |
| `0xD0..0xDF` | 2 | MIDI Channel Pressure | Copy. |
| `0xE0..0xEF` | 3 | MIDI Pitch Bend | Copy. |
| `0xF0 ... 0xF7` | variable | System Exclusive | Emit SMF sysex event. Preserve if possible. |
| `0xF1` | usually followed by filler byte | Start/no-op marker | Ignore for MIDI output. If followed by a low byte such as `00`, skip it. |
| `0xF2` | 1 | End of E-SEQ stream | Stop parsing; emit MIDI end-of-track by selected policy. |
| `0xF3 xx` | 2 | Short delay | Add `xx` ticks. |
| `0xF4 lo hi` | 3 | Long delay | Add `(hi << 7) | (lo & 0x7F)` ticks. |
| `0xF9 a b` | 3 | Bar/time marker | In samples, `F9 04 02` marks 4/4 barlines. Usually convert to MIDI time-signature metadata, not repeated channel data. |
| `0xFB lo hi` | 3 | Tempo factor | Emit MIDI tempo change at current tick. |
| `0xFF cc` | 2 | MIDI channel-prefix equivalent | Emit MIDI meta `FF 20 01 cc`. |
| Other `0xF*` | variable/open | Reserved/unknown | Preserve in diagnostic representation; do not silently drop in archival mode. |

### 7.3 Delay encoding

Short delay:

```text
F3 xx
```

Long delay:

```text
F4 lo hi
value = (hi << 7) | (lo & 0x7F)
```

Helpers:

```python
def decode_eseq_delay15(lo, hi):
    return (hi << 7) | (lo & 0x7F)

def encode_eseq_delay15(value):
    if not 0 <= value <= 0x3FFF:
        raise ValueError("delay too large for one F4")
    return value & 0x7F, (value >> 7) & 0x7F
```

Delay examples:

| Bytes | Ticks | Meaning |
|---|---:|---|
| `F3 60` | 96 | Short delay. |
| `F4 00 0C` | 1536 | One 4/4 measure at 384 PPQN. |
| `F4 64 02` | 356 | `(0x02 << 7) + 0x64`. |

Delay writer:

```python
def write_eseq_delay(out, delta):
    while delta > 0:
        if delta <= 255:
            out.extend([0xF3, delta])
            delta = 0
        else:
            chunk = min(delta, 0x3FFF)
            out.extend([0xF4, chunk & 0x7F, (chunk >> 7) & 0x7F])
            delta -= chunk
```

### 7.4 Bar marker `F9`

The 21 matched files and many normal files begin with a start marker and a bar marker:

```text
F1 00 F9 04 02
```

In the tested 4/4 files, later `F9 04 02` markers occur at barlines. At `384` PPQN:

```text
4 quarter notes/bar * 384 ticks/quarter = 1536 ticks/bar
```

MIDI equivalent at tick 0:

```text
00 FF 58 04 04 02 18 08
```

For non-4/4, use this provisional encoding until a corpus proves otherwise:

```text
F9 numerator denominator_exponent
bar_ticks = numerator * 384 * 4 / natural_denominator
natural_denominator = 2 ** denominator_exponent
```

### 7.5 End marker `F2`

`F2` ends the E-SEQ event stream. Bytes after `F2` are slack/padding for file/disk layout and must not be interpreted as musical events.

MIDI end-of-track policy is selectable:

| Policy | MIDI EOT tick |
|---|---:|
| `trim` | Last emitted MIDI event tick. Good for clean `.mid` output. |
| `preserve_eseq_end` | Tick at E-SEQ `F2`. Good for round-trip and bar-tail preservation. |
| `historical_exe` | Match the old converter style as closely as possible. |

---

## 8. Tempo conversion

### 8.1 Initial tempo: E-SEQ to MIDI

For the tested normal variant, initial tempo byte is `eseq[0x33]`.

```python
base_bpm = eseq[0x33] + 29
mpqn = 60000000 // base_bpm
```

MIDI tempo event:

```text
00 FF 51 03 <mpqn as 3-byte big-endian integer>
```

Examples from the 21 matched files:

| E-SEQ byte | Base BPM | MPQN decimal | MPQN hex | MIDI tempo payload |
|---:|---:|---:|---|---|
| `0x58` | 117 | 512820 | `0x07D334` | `07 D3 34` |
| `0x27` | 68 | 882352 | `0x0D76B0` | `0D 76 B0` |

In the matched set:

| Files | Header byte | Base BPM | Tempo changes |
|---|---:|---:|---:|
| 01-05 and 14-21 | `0x58` | 117 | 0 |
| 06-13 | `0x27` | 68 | 0 |

### 8.2 Initial tempo: MIDI to E-SEQ

For a MIDI file with initial `MPQN`:

```python
desired_bpm = 60000000 / mpqn
base_bpm = round(desired_bpm)
tempo_byte = base_bpm - 29
```

The normal one-byte tempo range is:

```text
tempo_byte = 0..255
base_bpm = 29..284
```

If the desired BPM is outside range or not close enough to an integer, choose a legal base BPM and emit a tick-0 `FB` tempo factor.

### 8.3 In-stream tempo changes: `FB lo hi`

Decode:

```python
factor = (hi << 7) | (lo & 0x7F)
effective_bpm = base_bpm * factor / 1000
mpqn = (60000000 * 1000) // (base_bpm * factor)
```

Encode from a desired effective BPM:

```python
factor = round(1000 * desired_bpm / base_bpm)
factor = max(1, min(0x3FFF, factor))
lo = factor & 0x7F
hi = (factor >> 7) & 0x7F
emit FB lo hi
```

Examples at `base_bpm = 117`:

| Factor | Effective BPM | Use |
|---:|---:|---|
| 1000 | 117.0 | No change. |
| 500 | 58.5 | Half speed. |
| 2000 | 234.0 | Double speed. |

### 8.4 Tempo precision and validation

E-SEQ tempo precision is:

```text
effective_bpm = integer_base_bpm * integer_factor / 1000
```

MIDI precision is integer `MPQN`. Exact round-trip of arbitrary MIDI tempo maps is therefore not guaranteed. Store diagnostics:

```text
source_mpqn
source_bpm
chosen_base_bpm
chosen_factor
output_mpqn
bpm_error
mpqn_error
```

### 8.5 Do not infer tempo from note spacing

Tempo is a header/tempo-map property. Event delays are tick counts. Do not attempt to derive E-SEQ tempo from note spacing or from `PIANODIR` duration display fields.

---

## 9. Channel events, piano channels, and CC7 playback-control warning

### 9.1 Direct MIDI channel-event mapping

For normal musical conversion, copy MIDI channel messages between E-SEQ and MIDI:

| MIDI/E-SEQ event | Copy rule |
|---|---|
| Note On / Note Off | Copy status and data bytes. |
| Control Change | Copy status and data bytes unless a user-selected policy rewrites known playback-control events. |
| Program Change | Copy or omit according to compatibility mode. |
| Pressure / Pitch Bend | Copy. |
| Sysex | Preserve as sysex where possible. |

The supplied personal E-SEQ files and reference MIDI pairs match musically after ignoring nonmusical metadata and a dummy tick-0 `C0 00` program-change event in the MIDI files.

### 9.2 Piano channel assumptions

In most Disklavier-prepared files, channel 0 is the main piano channel, but do not hard-code this as universal. APS MIDI Prep Tool should determine piano channels by configurable policy:

1. Explicit user setting.
2. Channels containing most note events in the piano key range.
3. Channels named or assigned to piano by track/instrument metadata.
4. Default to channel 0 if no better evidence exists.

### 9.3 Early CC7 = 0 warning and policies

A MIDI file may contain normal piano note data but also send `CC7 Channel Volume = 0` near the beginning of the piano channel:

```text
B0 07 00    ; channel 0, controller 7, value 0
```

A standards-compliant MIDI player or synthesizer will treat controller 7 as channel volume, and value zero may mute that channel. Such a file can appear silent in generic playback even though note events are present.

In a Disklavier/E-SEQ context, `CC7 = 0` may have one of several meanings:

- Intentional Yamaha-specific playback setup.
- A compatibility barrier or crude copy-protection behavior.
- A conversion artifact where Yamaha hardware or Yamaha-oriented software restores, ignores, or interprets the volume differently.
- A legitimate musical mute, though this is less likely if dense piano note data follows and no later volume restoration occurs.

APS MIDI Prep Tool should flag this condition when all are true:

```text
controller == 7
value == 0
channel has note events later in the same stream
no later CC7 restoration before the first notes, or restoration is ambiguous
```

Recommended policies:

| Policy | Behavior |
|---|---|
| `preserve` | Leave `CC7=0` unchanged. Best for archival conversion. |
| `warn_only` | Preserve but report a warning. Default for analysis. |
| `playback_fix_100` | Rewrite un-restored `CC7=0` before later notes to `CC7=100`. Good for generic MIDI preview. |
| `playback_fix_127` | Rewrite to `127`. Louder preview; less conservative. |
| `drop_early_cc7_zero` | Remove the mute event. Legacy name retained for compatibility. |
| `yamaha_profile` | Apply a future Yamaha-specific rule if proven by hardware/corpus behavior. |

Store this in conversion reports because it may explain “silent MIDI” complaints.

---

## 10. E-SEQ to MIDI conversion algorithm

### 10.1 High-level algorithm

```python
def eseq_to_midi(data, options):
    variant = detect_eseq_variant(data)
    header = parse_eseq_header(data, variant)
    start = event_start_for_variant(header, variant)
    events = parse_eseq_event_stream(data, start)

    midi = MidiFile(format=0, division=384)
    track = MidiTrack()

    # Tick 0 metadata.
    track.add_meta(0, set_tempo(header.initial_mpqn))
    if options.write_time_signature:
        track.add_meta(0, time_signature(header.time_signature or (4, 4)))
    if options.write_key_signature:
        track.add_meta(0, key_signature_c_major())
    if options.write_source_text:
        track.add_meta(0, text("Converted from Disklavier E-SEQ"))
    if options.write_instrument_name:
        track.add_meta(0, instrument_name("Piano"))
    if options.write_track_name and header.title:
        track.add_meta(0, track_name(header.title))
    if options.insert_program_change:
        track.add_channel(0, 0xC0, [0x00])

    for ev in events:
        if ev.kind == "midi_channel":
            ev = apply_cc7_policy_if_needed(ev, options)
            track.add_channel(ev.tick, ev.status, ev.data)
        elif ev.kind == "tempo_factor":
            track.add_meta(ev.tick, set_tempo(factor_to_mpqn(header.base_bpm, ev.factor)))
        elif ev.kind == "channel_prefix":
            track.add_meta(ev.tick, channel_prefix(ev.channel))
        elif ev.kind == "sysex":
            track.add_sysex(ev.tick, ev.payload)
        elif ev.kind in ("bar", "start", "noop"):
            continue
        elif ev.kind == "unknown":
            report_unknown(ev)

    track.add_meta(select_eot_tick(events, options), end_of_track())
    midi.tracks.append(track)
    return midi.to_bytes()
```

### 10.2 Recommended MIDI metadata modes

| Mode | Metadata behavior |
|---|---|
| `minimal_exe` | Tempo and optional track name only; closest to the old E-SEQ-to-MIDI converter. |
| `reference_pairs` | Tempo, time signature 4/4, key C, text `Converted from Disklavier floppy`, instrument name `Piano`, optional tick-0 `C0 00`. |
| `clean_canonical` | Tempo, time signature, track name/title, end-of-track; minimal but friendly. |
| `archival_verbose` | Include source text, conversion diagnostics, and preserve sysex/channel-prefix events. |

### 10.3 MIDI running status

The historical converter may use running status. APS MIDI Prep Tool may write full status bytes or use running status. Both are valid SMF encodings. Regression tests should compare parsed MIDI event lists, not raw track bytes, unless testing a byte-compatibility mode.

---

## 11. MIDI to E-SEQ conversion algorithm

### 11.1 Normalize the MIDI input

1. Parse `MThd` and all `MTrk` chunks.
2. Accept format 0 and format 1. Reject or explicitly opt in to format 2 because independent sequences do not map cleanly to one E-SEQ song.
3. Convert MIDI delta-times to absolute ticks.
4. Resolve running status.
5. Merge type 1 tracks into one stable, time-sorted event list.
6. Extract tempo map, time signatures, key signatures, channel events, sysex, text, and track/instrument names.
7. Convert timing to 384 PPQN.

Tick-grid conversion:

```python
eseq_tick = round(midi_tick * 384 / midi_division)
```

For SMPTE-timed MIDI divisions, require a special conversion mode because E-SEQ writer behavior is PPQN/music-grid oriented.

### 11.2 Select base tempo and tempo factors

1. Read the first MIDI tempo event, or assume 120 BPM if absent.
2. Choose `base_bpm` as a legal integer BPM.
3. Write normal header tempo byte:

   ```python
   header[0x33] = base_bpm - 29
   header[0x24] = header[0x33]
   ```

4. For each tempo event, including tick 0 if needed, write `FB lo hi` when the desired tempo differs from the base tempo beyond tolerance.

### 11.3 Convert MIDI events to E-SEQ events

| MIDI input | E-SEQ output | Notes |
|---|---|---|
| `8n kk vv` Note Off | `8n kk vv`, or `9n kk 00` by normalization policy | Both are valid MIDI-level note-off representations. |
| `9n kk vv` Note On | Copy | Preserve velocity. |
| `Bn cc vv` Control Change | Copy or policy-rewrite | Preserve pedal data; do not quantize continuous pedal unless requested. |
| `Cn pp` Program Change | Copy or omit | Many E-SEQ files omit tick-0 piano program change. |
| `Dn`, `En`, `An` | Copy | Supported by the parser. |
| `FF 51` Tempo | Header tempo and `FB` tempo factors | See section 8. |
| `FF 58` Time Signature | Header display bytes and `F9` markers | 4/4 proven; other meters provisional. |
| `FF 59` Key Signature | Drop or store only in external metadata | No proven normal E-SEQ field. |
| Track/text/instrument names | 32-byte title field if appropriate | Use conservative single-byte encoding. |
| `FF 20 01 cc` Channel Prefix | `FF cc` | The SMF spec notes this capability is also present in Yamaha ESEQ. |
| Sysex | `F0 ... F7` | Preserve where possible. |
| Other meta events | Drop/report | No proven E-SEQ representation. |

### 11.4 Same-tick ordering

Same-tick ordering can affect playback setup. Recommended order when writing E-SEQ events at the same tick:

1. Bar marker `F9`, if exactly at a barline.
2. Tempo factor `FB`.
3. Program/bank/setup controllers.
4. Pedal and other continuous controllers.
5. Notes.
6. Sysex, unless the sysex is known to be setup data that must precede controllers.

When round-tripping an existing MIDI file, preserve original same-tick order as much as possible.

### 11.5 Emit delays and bar markers

Maintain `current_tick`. Before each output event, emit delay opcodes sufficient to reach the target tick. If inserting bar markers, split delays around barlines.

For 4/4:

```python
bar_ticks = 4 * 384
```

At file start, write:

```text
F1 00 F9 04 02
```

Then write `F9 04 02` at later barlines if using bar-marker mode, but do not write generated bar markers before the first real stream event or at the exact final `F2`/end tick. `EEXPLORE.EXE`'s Song Properties dialog reads the visible before-delay from the physical opcode immediately after `F1 00`, and the visible after-delay from the physical opcode immediately before `F2`. If those positions contain `F9` rather than an `F4` delay, the dialog can display zero even though the musical tick stream still contains silence.

### 11.6 Choose the E-SEQ end tick

Suggested policies:

| End policy | E-SEQ `F2` tick |
|---|---:|
| `last_event` | Last musical/setup event tick. |
| `midi_eot` | MIDI end-of-track tick after scaling. |
| `next_bar` | Next barline after last event; best old-media compatibility. |
| `fixed_tail` | Last event plus a configured silence tail. |

For old Disklavier disk preparation, `next_bar` is a good default.

### 11.7 Build a normal `.FIL` header

Recommended writer strategy:

1. Start with a known-good normal `0x77`-byte header template.
2. Set `FE` and `COM-ESEQ` explicitly.
3. Write filename bytes at `0x27..0x31`.
4. Write tempo at `0x24` and `0x33`.
5. Write time signature at `0x34..0x35`.
6. Write title at `0x57..0x76`.
7. Write event stream beginning at `0x77`.
8. After writing `F2`, update stream length, duration/end tick, event-class flags, and padding.

Generated-file update example:

```python
f2_offset = len(header) + len(event_stream) - 1
stream_len = f2_offset + 1 - 0x77

write_u32_le(header, 0x03, f2_offset + 1)       # converter-style generated files
write_u32_le(header, 0x1F, stream_len)
write_u32_le(header, 0x37, end_tick)
header[0x43:0x47] = bytes([0x00, 0x77, 0x00, 0x00])
header[0x51] = 1 if has_controllers else 0
header[0x54] = (0x01 if has_notes else 0) | (0x04 if has_controllers else 0)
```

Because several header fields remain opaque, a generated E-SEQ writer should identify itself as using a known template profile, for example `normal_0x77_generated`.

---

## 12. `PIANODIR.FIL` role and disk behavior

### 12.1 Role

`PIANODIR.FIL` is the disk/album index for E-SEQ media. It is not required to convert one `.FIL` file to one `.mid` file, but it is required for old Disklavier-style media preparation because the piano uses it as the active song list and display-order source.

It stores:

- Active song records, up to 60 slots.
- DOS 8.3 filenames.
- Display titles.
- Tempo bytes and copied E-SEQ header metadata.
- Duration totals and count fields.
- Disk/album title.

### 12.2 Practical media rules

Public DKVUTILS-era descriptions and the supplied images agree on these practical rules:

| Rule | Implementation consequence |
|---|---|
| Old E-SEQ Disklavier disks are 720 KB / 2DD-style media. | Do not assume 1.44 MB HD layout for physical old-media writing. |
| Original disks may have invalid data in the first sector. | Normal PC mounting may fail; use disk-image tools for originals. |
| `PIANODIR.FIL` is normally 6 KB. | Write 6144 bytes by default. |
| The index controls active songs and order. | Root directory order alone is insufficient. |
| E-SEQ disks use `.FIL` plus `PIANODIR.FIL`; MIDI disks are a different workflow. | Do not mix MIDI and E-SEQ directory conventions. |

### 12.3 Observed FAT12-style disk-image layout

The supplied Yamaha images are normally `737,280` bytes, consistent with 720 KB media. They have an intentionally unusable first sector, but FAT and root-directory structures are parsable with fixed parameters:

| Region | Offset | Size | Notes |
|---:|---:|---:|---|
| Sector 0 | `0x0000` | 512 | Not a valid DOS BPB; often `E5` or bad-sector filler. |
| FAT #1 | `0x0200` | 1536 | Starts with `F9 FF FF`, consistent with FAT12 media descriptor. |
| FAT #2 | `0x0800` | 1536 | Duplicate FAT. |
| Root directory | `0x0E00` | 3584 | 112 DOS 32-byte directory entries. |
| Data area | `0x1C00` | remainder | Cluster 2 begins here; cluster size 1024 bytes. |

A robust tool should locate `PIANODIR.FIL` by root-directory name and follow its FAT chain. Do not assume it is root entry 0 or starts at cluster 2, even though that is common.

---

## 13. `PIANODIR.FIL` binary format

### 13.1 Top-level layout

Common 6144-byte form:

```text
Offset      Size        Meaning
0x0000      16          PIANODIR header
0x0010      80 * 60     Song records 0..59
0x12D0      80          Disk-info / disk-title record
0x1320      1248        Reserved/slack, usually zero in generated indexes
```

5120-byte form:

```text
0x0000..0x131F      Same defined region through disk-info record
0x1320..0x13FF      Short slack
```

Reader: accept both `0x1400` and `0x1800` if the header is valid.  
Writer: create `0x1800` unless byte-preserving an existing image.

### 13.2 Header

The valid corpus and `EEXPLORE.EXE` contain this 16-byte constant:

```text
FE 00 00 00 14 00 00 50 49 41 4E 4F 44 49 52 00
```

ASCII interpretation:

```text
FE 00 00 00 14 00 00 "PIANODIR" 00
```

`0x14` is consistent with the logical `0x1400` size even when the file's FAT directory entry stores `0x1800` bytes.

### 13.3 Song-record layout

Each song record is 80 bytes (`0x50`).

| Record offset | Size | Meaning |
|---:|---:|---|
| `0x00` | 11 | DOS 8.3 short filename bytes: 8-byte base + 3-byte extension, space padded. |
| `0x0B` | 1 | Usually `00` separator. |
| `0x0C` | 1 | Base tempo byte; normal `base_bpm = byte + 29`. |
| `0x0D` | 3 | Time signature / opaque E-SEQ metadata copied from header. |
| `0x10` | 4 | Duration/end-tick field, little-endian. `EEXPLORE` displays seconds as value / 750. |
| `0x14` | 2 | Delay before first note, little-endian E-SEQ ticks. Display milliseconds as `ticks * 1000 / 750`. If a song has no note-on events, fall back to the first real event. |
| `0x16` | 2 | Per-song secondary word; summarized in disk-info field `0x44` in most indexes. |
| `0x18` | 2 | Delay after last real event, little-endian E-SEQ ticks. Display milliseconds as `ticks * 1000 / 750`. |
| `0x1A` | 2 | Opaque playback/header metadata; copy. |
| `0x1C` | 4 | Event-start/helper field. Normal record shows `00 77 00 00`; Q11 record shows `02 00 00 00`. |
| `0x20` | 8 | Opaque constants/metadata; copy. |
| `0x28` | 1 | Write-protect flag. Bit `0x80` set means write-protected; `0x00` means write-protect off. Mirrors file offset `0x4F` in normal E-SEQ files. |
| `0x29` | 1 | Low two bits are arrangement/type display code. Mirrors file offset `0x50` in normal E-SEQ files. |
| `0x2A` | 1 | Opaque flag. 95 corpus records differed here from the source file's header slice. |
| `0x2B` | 5 | Opaque metadata; copy. |
| `0x30` | 32 | Display title, often two 16-character display lines. |

Type display from `record[0x29] & 0x03`:

| Code | Display |
|---:|---|
| 0 | Solo |
| 1 | L-R Split |
| 2 | Ensemble |
| 3 | ??? |

The `Solo - LR Split - Combo` sample confirms this directly inside the 0x50-byte header/catalog record: `VAR` and `RONDO` changed this type byte from `0` to `1`, while `ROMANCE` and `MOMENTS` changed it from `0` to `2`; unchanged Solo tracks kept `0`. This confirms that code `2` is Ensemble for both solo-style and controller-rich source files.

Write-protect behavior from the `Write Protect` sample:

| Track | Arrangement | Write-protect byte |
|---|---|---:|
| `VAR` | L-R Split | `0x00` off |
| `ORGAN` | Solo | `0x00` off |
| `MOMENTS` | Ensemble | `0x00` off |
| unchanged comparison tracks | Solo/L-R Split/Ensemble | `0x80` on |

The tested program writes the same value at E-SEQ file offset `0x4F` and `PIANODIR` record offset `0x28`. APS MIDI Prep Tool reads and preserves this flag for display/catalog generation; it does not enforce it as a local editing lock.

### 13.4 Normal `PIANODIR` song-record generation

For a normal E-SEQ file:

```python
record = bytearray(eseq_file[0x27:0x77])
record[0:11] = dos_8_3_short_name_bytes
record[0x10:0x14] = end_tick.to_bytes(4, "little")
record[0x14:0x16] = delay_before_ticks.to_bytes(2, "little")
record[0x18:0x1A] = delay_after_ticks.to_bytes(2, "little")
# record[0x29] is copied from file[0x50], preserving Solo/L-R Split/Ensemble.
# record[0x28] is copied from file[0x4F], preserving write-protect on/off.
```

Derive the timing values from the parsed event stream:

```python
end_tick = tick_at_F2
if note_on_events:
    delay_before_ticks = min(tick for tick, event in note_on_events)
elif real_events:
    delay_before_ticks = min(tick for tick, event in real_events)
else:
    delay_before_ticks = 0
delay_after_ticks = end_tick - max(tick for tick, event in real_events) if real_events else 0
```

For before-delay, prefer the first note-on event so pre-roll setup messages at tick 0 do not make the displayed lead-in become zero. `real_events` means channel events, SysEx, and other non-marker playback events. Do not count `F1`, `F9`, `FB`, padding, or the `F2` terminator as real events for after-delay calculation.

This corrected rule is backward compatible with the original corpus rule: most source files already have matching header timing bytes, so a slice-copy reproduces them. The `Different Delays` sample set proves that third-party tooling may update the event stream while leaving copied header/catalog delay words stale. In that set, the first three tracks changed to:

| Track | Before ticks | Before display | After ticks | After display |
|---|---:|---:|---:|---:|
| `FLUTE` | 4365 | 5820 ms | 6315 | 8420 ms |
| `VAR` | 4365 | 5820 ms | 6315 | 8420 ms |
| `ROMANCE` | 4365 | 5820 ms | 6315 | 8420 ms |

`display_ms = ticks * 1000 / 750`, matching `EEXPLORE`'s `secs/1000` fields. Integer displays observed in the Windows tool truncate fractional milliseconds.

With the timing refresh omitted, the older slice-copy rule exactly reproduced 1,116 of 1,211 normal records in the valid corpus. The remaining 95 differed only at `record[0x2A]`, where original `PIANODIR.FIL` had `0x00` and the source E-SEQ header had `0x01`. Because the field is not display-critical and `EEXPLORE.EXE` copies the source slice, the best new-index behavior is to copy the source byte rather than force zero.

### 13.5 Q11V1.00 `PIANODIR` record generation

For all 28 observed Q11 records, this recipe reproduced the original `PIANODIR` record byte-for-byte:

```python
record = bytearray(80)
record[0x00:0x0B] = dos_8_3_short_name_bytes
record[0x0B] = 0x00
record[0x0C] = eseq_file[0x24]       # Q11 tempo byte
record[0x1C:0x20] = bytes.fromhex("02 00 00 00")
record[0x20:0x24] = bytes.fromhex("10 7F 00 00")
record[0x24:0x28] = bytes.fromhex("41 01 00 00")
record[0x28:0x2C] = bytes.fromhex("00 02 00 00")
record[0x30:0x50] = eseq_file[0x57:0x77]
```

Q11 records in the corpus have zero duration fields.

### 13.6 Disk-info / disk-title record

The disk-info record begins at `0x12D0`.

| Disk-info offset | File offset | Size | Meaning |
|---:|---:|---:|---|
| `0x00` | `0x12D0` | 64 | Disk/album title, raw single-byte text. |
| `0x40` | `0x1310` | 4 | Total duration accumulator, little-endian. |
| `0x44` | `0x1314` | 2 | Secondary aggregate word. |
| `0x46` | `0x1316` | 2 | Count field, normally active song count + 1. |
| `0x48` | `0x1318` | 8 | Reserved/slack. |

Total duration:

```python
pianodir_total = sum(little_u32(record[0x10:0x14]) for active records) & 0xFFFFFFFF
```

This matched every valid index in the corpus.

Secondary aggregate, default writer rule:

```python
aggregate = sum(little_u16(record[0x16:0x18]) for active records)
write_u16_le(min(aggregate, 0xFFFF))
```

This saturated-sum rule matched most factory-style indexes. Some indexes store zero or a wrapped low word; readers should not depend on this field for playback.

Count field:

```python
write_u16_le(active_song_count + 1)
```

The `+1` appears to include `PIANODIR.FIL` itself in the file count.

### 13.7 Reader algorithm

```python
def parse_pianodir(data):
    assert data[0:16] == PIANODIR_HEADER
    records = []
    for slot in range(60):
        off = 0x10 + slot * 0x50
        rec = data[off:off+0x50]
        if rec[0] in (0x00, 0xE5, 0xFF):
            break
        records.append({
            "slot": slot,
            "filename": decode_dos_8_3(rec[0:11]),
            "tempo_byte": rec[0x0C],
            "base_bpm": rec[0x0C] + 29,
            "duration_raw": le32(rec[0x10:0x14]),
            "display_seconds": le32(rec[0x10:0x14]) / 750,
            "type_code": rec[0x29] & 3,
            "title": decode_title(rec[0x30:0x50]),
        })
    disk_title = decode_title(data[0x12D0:0x1310])
    total_duration = le32(data[0x1310:0x1314])
    count_field = le16(data[0x1316:0x1318])
    return records, disk_title, total_duration, count_field
```

### 13.8 Writer algorithm

```python
def build_pianodir(eseq_files, disk_title=""):
    out = bytearray(0x1800)
    out[0:16] = PIANODIR_HEADER

    total_duration = 0
    aggregate = 0

    for slot, item in enumerate(eseq_files[:60]):
        data = item.bytes
        short_name = encode_dos_8_3(item.filename)

        if data[0x07:0x0F] != b"COM-ESEQ":
            raise ValueError("not an E-SEQ file")

        if data[0x0F:0x17] == b"Q11V1.00":
            rec = make_q11_pianodir_record(data, short_name)
        else:
            rec = bytearray(data[0x27:0x77])
            rec[0:11] = short_name

        off = 0x10 + slot * 0x50
        out[off:off+0x50] = rec
        total_duration = (total_duration + le32(rec[0x10:0x14])) & 0xFFFFFFFF
        aggregate += le16(rec[0x16:0x18])

    title_bytes = encode_title(disk_title, 64)
    out[0x12D0:0x12D0+64] = title_bytes
    write_u32_le(out, 0x1310, total_duration)
    write_u16_le(out, 0x1314, min(aggregate, 0xFFFF))
    write_u16_le(out, 0x1316, min(len(eseq_files), 60) + 1)

    return bytes(out)
```

---

## 14. Disk-image authoring workflow

A complete MIDI-to-old-Disklavier workflow should be:

1. Normalize and validate input MIDI.
2. Convert each MIDI file to a `.FIL` E-SEQ file.
3. Assign uppercase DOS 8.3 filenames, commonly `PIANO001.FIL`, `PIANO002.FIL`, etc., unless preserving original names.
4. Build `PIANODIR.FIL` from the resulting `.FIL` files.
5. Write a Yamaha-compatible 720 KB image or target folder/device.
6. Keep E-SEQ disks E-SEQ-only; do not mix `.MID` and `.FIL` media conventions.
7. Validate by parsing the final image and confirming `PIANODIR` records point to visible files by name.

For a raw disk image matching the observed corpus, use:

```text
image_size = 737280
sector_size = 512
sectors_per_cluster = 2
fat_count = 2
fat_size = 1536 bytes
root_dir_offset = 0x0E00
root_dir_entries = 112
data_area_offset = 0x1C00
cluster_2_offset = 0x1C00
```

When possible, use existing disk-image tooling rather than hand-writing FAT12 unless APS MIDI Prep Tool explicitly owns the disk-image writer.

---

## 15. Public Disklavier disk-image constants

The public `disklav.py` project independently identifies PianoSoft Plus constants that align with this analysis when `PIANODIR.FIL` begins at cluster 2:

```text
PianoSoft Plus disk title raw image offset: 0x2ED0
PIANODIR-relative disk title offset:        0x12D0
TOC/song-record raw image offset:           0x1C40
PIANODIR-relative record offset:            0x0010
TOC stride:                                 80 bytes
TOC title length:                           32 bytes
Track start signature:                      FE 00 00
Track end signature:                        F2 00 00
```

This agrees with:

```text
cluster_2_raw_offset + 0x12D0 = 0x1C00 + 0x12D0 = 0x2ED0
cluster_2_raw_offset + 0x0010 = 0x1C00 + 0x0010 = 0x1C10
```

`disklav.py` uses `0x1C40` for TOC scanning because it scans title fields within each 80-byte record, not the start of each record.

---

## 16. Validation strategy for the future large corpus

When several thousand matched pairs are available, run four levels of validation.

### 16.1 Structural E-SEQ validation

For every `.FIL`:

- Signature and variant.
- Event-start offset.
- Header length fields vs actual `F2` offset.
- Padding/slack pattern.
- Tempo byte(s) and inferred BPM.
- `FB` tempo-factor positions.
- `F9` bar-marker positions and intervals.
- Unknown opcodes.
- Header field correlations: duration, event count, note/controller flags, title length, file size.

### 16.2 Musical MIDI validation

Normalize source and converted MIDI to:

```text
(abs_tick, kind, status_or_meta_type, payload)
```

Compare:

- Tempo map.
- Time signatures.
- Channel events.
- Sysex events.
- End tick under selected policy.

Normalize away harmless byte differences:

- Running status vs full status.
- Equivalent note-off encodings.
- Metadata ordering when not musically meaningful.
- Optional tick-0 program change.

### 16.3 PIANODIR validation

For every disk image:

- Parse FAT root directory.
- Locate `PIANODIR.FIL` by name.
- Parse 60 records.
- Match each active record filename to a file entry.
- Rebuild `PIANODIR.FIL` from `.FIL` files.
- Compare original vs rebuilt:
  - Exact match for Q11 recipe records.
  - Normal records match except known `0x2A` differences.
  - Total duration and count match.
  - Disk title matches.

### 16.4 Playback-control validation

For every MIDI pair:

- Detect `CC7=0` on channels with later notes and no intervening positive CC7 restoration.
- Record whether notes follow before volume restoration.
- Test whether E-SEQ conversion preserves, removes, or rewrites the event.
- Compare audible generic MIDI playback before/after policy modes.
- If hardware results are available, determine whether Disklavier playback ignores or honors the mute.

---

## 17. Recommended APS MIDI Prep Tool architecture

### 17.1 Modules

```text
aps_eseq/variant.py       Variant detection and header parsing
aps_eseq/events.py        E-SEQ event-stream parser/writer
aps_eseq/tempo.py         Tempo byte/factor/MPQN helpers
aps_eseq/midi.py          SMF parser/writer integration
aps_eseq/convert.py       E-SEQ <-> MIDI conversion workflows
aps_eseq/pianodir.py      PIANODIR parser/writer
aps_eseq/diskimage.py     Optional Yamaha FAT12-style image reader/writer
aps_eseq/report.py        Diagnostics, warnings, corpus reports
```

### 17.2 Core data classes

```python
@dataclass
class ESeqSong:
    variant: str
    filename_8_3: bytes
    title: str
    base_bpm: int
    tempo_byte: int
    time_signature: tuple[int, int]
    event_start: int
    events: list[ESeqEvent]
    raw_header: bytes
    diagnostics: list[str]

@dataclass
class ESeqEvent:
    tick: int
    kind: str
    status: int | None = None
    data: bytes = b""
    value: int | None = None

@dataclass
class PianoDirEntry:
    slot: int
    filename_8_3: bytes
    title: str
    tempo_byte: int
    duration_raw: int
    type_code: int
    raw_record: bytes
```

### 17.3 User-facing conversion options

| Option | Values |
|---|---|
| MIDI output metadata | `minimal_exe`, `reference_pairs`, `clean_canonical`, `archival_verbose` |
| End tick policy | `trim`, `preserve_eseq_end`, `next_bar`, `midi_eot` |
| Note-off policy | `preserve`, `normalize_to_note_on_zero`, `normalize_to_8n` |
| CC7 zero policy | `preserve`, `warn_only`, `playback_fix_100`, `playback_fix_127`, `drop_early_cc7_zero` |
| Padding policy | `compat_f6`, `zero`, `compact`, `preserve` |
| PIANODIR size | `0x1800` default, `preserve_existing` for edits |
| Filename policy | `uppercase_8_3`, `preserve_case_8_3`, `auto_piano001` |

---

## 18. Minimal reference parser pseudocode

### 18.1 E-SEQ parser

```python
def detect_eseq_variant(data):
    if data[0x07:0x0F] != b"COM-ESEQ":
        raise ValueError("missing COM-ESEQ signature")
    if data[0x0F:0x17] == b"Q11V1.00":
        return "q11"
    return "normal_0x77"


def event_start_for_variant(data, variant):
    if variant == "q11":
        return 0x0200
    return 0x0077


def parse_eseq_events(data, start):
    tick = 0
    i = start
    events = []

    while i < len(data):
        b = data[i]
        i += 1

        if b < 0x80:
            events.append(ESeqEvent(tick, "filler", value=b))
            continue

        hi = b & 0xF0

        if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            d1, d2 = data[i], data[i+1]
            i += 2
            events.append(ESeqEvent(tick, "midi_channel", b, bytes([d1, d2])))

        elif hi in (0xC0, 0xD0):
            d1 = data[i]
            i += 1
            events.append(ESeqEvent(tick, "midi_channel", b, bytes([d1])))

        elif b == 0xF0:
            payload_start = i
            while i < len(data) and data[i] != 0xF7:
                i += 1
            if i < len(data):
                i += 1
            events.append(ESeqEvent(tick, "sysex", 0xF0, data[payload_start:i]))

        elif b == 0xF1:
            if i < len(data) and data[i] < 0x80:
                i += 1
            events.append(ESeqEvent(tick, "start_marker"))

        elif b == 0xF2:
            events.append(ESeqEvent(tick, "end"))
            break

        elif b == 0xF3:
            delta = data[i]
            i += 1
            tick += delta

        elif b == 0xF4:
            lo, hi2 = data[i], data[i+1]
            i += 2
            tick += (hi2 << 7) | (lo & 0x7F)

        elif b == 0xF9:
            a, c = data[i], data[i+1]
            i += 2
            events.append(ESeqEvent(tick, "bar", 0xF9, bytes([a, c])))

        elif b == 0xFB:
            lo, hi2 = data[i], data[i+1]
            i += 2
            factor = (hi2 << 7) | (lo & 0x7F)
            events.append(ESeqEvent(tick, "tempo_factor", value=factor))

        elif b == 0xFF:
            channel = data[i]
            i += 1
            events.append(ESeqEvent(tick, "channel_prefix", value=channel))

        else:
            events.append(ESeqEvent(tick, "unknown", value=b))

    return events
```

### 18.2 MIDI variable-length quantity writer

```python
def write_vlq(value):
    if value < 0:
        raise ValueError("negative VLQ")
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))
```

### 18.3 Tempo helpers

```python
def normal_eseq_base_bpm(data):
    return data[0x33] + 29


def bpm_to_mpqn_floor(bpm):
    return 60000000 // bpm


def tempo_factor_to_mpqn(base_bpm, factor):
    if factor <= 0:
        raise ValueError("invalid E-SEQ tempo factor")
    return (60000000 * 1000) // (base_bpm * factor)


def mpqn_to_bpm(mpqn):
    return 60000000 / mpqn
```

---


## 19. Static tool findings

The supplied EXEs were not executed. They were inspected statically and used only as corroborating evidence.

### 19.1 `ESEQ2MID.EXE`

The supplied E-SEQ-to-MIDI converter corroborates the core MIDI conversion behavior.

| Finding | Detail |
|---|---|
| Purpose string | `ESEQ2MID - Converts Yamaha ESEQ format files to MIDI (type 0)` |
| Input signature | Checks for `COM-ESEQ` |
| MIDI output | Writes `MThd` / `MTrk`, SMF type 0, one track, 384 PPQN |
| Initial tempo source | Reads normal E-SEQ byte `file[0x33]` |
| Initial tempo formula | `mpqn = 60000000 // (file[0x33] + 29)` |
| Tempo-change opcode | Handles in-stream `FB lo hi` as a factor in thousandths |
| Tempo constant | Uses decimal `60000000` (`0x03938700`) |

Implementation conclusion: tempo is not inferred from note spacing. The converter uses the E-SEQ header tempo byte and optional in-stream `FB` tempo factors.

### 19.2 `EEXPLORE.EXE`

The supplied `EEXPLORE.EXE` is a third-party `PIANODIR.FIL` utility, not a Yamaha original source. It nevertheless corroborates the normal `PIANODIR` writer rule.

| Static behavior | Implementation implication |
|---|---|
| Embeds the 16-byte `PIANODIR` header constant | Confirms the generated-index signature bytes |
| Initializes and writes a 6144-byte index buffer | Confirms 6 KB writer default |
| Reads 6144 bytes from `PIANODIR.FIL` | Confirms generated-index parser size expectation |
| Reads `0x77` bytes from a candidate E-SEQ song | Confirms normal header length |
| Checks `COM-ESEQ` at file offset `0x07` | Confirms song signature location |
| Copies 80 bytes from song-file offset `0x27` | Confirms `record = file[0x27:0x77]` for normal files |
| Overwrites first 11 record bytes with actual DOS 8.3 filename | Confirms the filename patch rule |
| Uses 60 song slots | Confirms the `60 * 80` record area |
| Adds each record duration to an aggregate total | Confirms `PIANODIR+0x1310` total-duration construction |
| Divides aggregate duration by `750` for display | Confirms E-SEQ Explorer's duration display unit |
| Displays type from `record[0x29] & 3` | Confirms Solo/L-R Split/Ensemble display-code location |

Limitation: this static behavior describes ordinary non-Q11 E-SEQ files. It does not explain the Q11 record template, which comes from disk-image corpus evidence.

---

## 20. Known open questions

These items should be resolved with the next, larger corpus and, ideally, hardware playback tests:

1. Exact semantic meaning of all normal header bytes in `0x3B..0x56`.
2. Exact meaning of `PIANODIR` record byte `0x2A`.
3. Whether non-4/4 files encode `F9` exactly as `F9 numerator denominator_exponent`.
4. Whether every non-Q11 variant with factory marker strings such as `714003` uses the same event grammar.
5. Whether `0x03` length/control bytes have a variant-specific meaning beyond generated-file logical length.
6. Sysex streams with embedded timing boundaries.
7. Hardware behavior for un-restored `CC7=0` on piano channels.
8. Whether Disklavier playback uses `PIANODIR` duration fields for playback, display only, or both.
9. Behavior of compact unpadded `.FIL` files on old hardware.
10. Whether old hardware depends on `PIANODIR.FIL` being physically first on disk.

---

## 21. References

Public references used for context and cross-checking:

- Standard MIDI File format reference, including `FF 51 03` tempo, `FF 2F 00` end-of-track, `FF 58` time signature, and Yamaha ESEQ mention in the channel-prefix discussion: <https://midimusic.github.io/tech/midispec.html>
- MIDI Association, MIDI 1.0 Control Change Messages, including controller 7 as Channel Volume and controller 64 as Damper/Sustain: <https://midi.org/midi-1-0-control-change-messages>
- Yamaha FAQ, “Using E-SEQ Format on a Disklavier II XG”: <https://faq.yamaha.com/usa/s/article/U0001636>
- Alexander Peppe, “Converting MIDI Files and Creating PIANODIR.FIL for E-SEQ Files”: <https://www.alexanderpeppe.com/eseq-and-pianodir-fil/>
- Alexander Peppe, “Using PPFBU to Back Up Disks: Disklavier Floppy Disks, E-SEQ, and MIDI Formats”: <https://www.alexanderpeppe.com/disklavier-floppy-backups/>
- MS3FGX `disklav.py`, public Disklavier image reverse-engineering script: <https://github.com/MS3FGX/disklav/blob/master/disklav.py>
- YamahaMusicians forum post quoting DKVUTILS-era Disklavier disk and `PIANODIR.FIL` notes: <https://yamahamusicians.com/forum/viewtopic.php?t=14561>

---

## 22. Implementation checklist

Use this checklist before considering APS MIDI Prep Tool E-SEQ support complete:

- [ ] Parse SMF type 0 and type 1 with running status, meta events, sysex, and PPQN division.
- [ ] Normalize MIDI to one absolute-tick event list.
- [ ] Detect E-SEQ normal vs Q11 variant.
- [ ] Parse E-SEQ event stream with `F3`, `F4`, `F9`, `FB`, channel events, sysex, and `F2`.
- [ ] Convert normal E-SEQ tempo byte and `FB` factors to MIDI tempo events.
- [ ] Convert MIDI tempo map to normal E-SEQ tempo byte and `FB` factors.
- [ ] Support selectable end-tick policies.
- [ ] Detect and report un-restored piano-channel `CC7=0` events.
- [ ] Provide CC7 preservation/playback-fix policies.
- [ ] Generate normal `0x77` `.FIL` files with coherent header, event stream, `F2`, and padding.
- [ ] Parse `PIANODIR.FIL` size `0x1400` and `0x1800`.
- [ ] Generate `0x1800` `PIANODIR.FIL` indexes.
- [ ] Implement normal PIANODIR record recipe.
- [ ] Implement Q11 PIANODIR record recipe.
- [ ] Recalculate disk title, total duration, secondary aggregate, and count fields.
- [ ] Validate against the 21 personal pairs.
- [ ] Validate against the 110-image PIANODIR corpus.
- [ ] Add corpus-based tests for thousands of future pairs.
