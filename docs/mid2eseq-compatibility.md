# MID2ESEQ compatibility

New MIDI-to-Disklavier conversions follow the timing and file conventions of
MID2ESEQ 2.01, with Yamaha binary and continuous pedal conversion. Timing uses
supplied MIDI/MID2ESEQ reference pairs; pedal conversion follows the recovered
Yamaha Mark IV converter, including an independent executable comparison with
its original pedal routine.

## Conversion behavior

- Integrate MIDI tempo changes into elapsed time before selecting E-SEQ ticks.
  Use the legacy integer arithmetic and a constant 117 BPM header, corresponding
  to 748.8 ticks per second. No generated FB tempo or F9 bar commands are written.
- Reproduce the legacy preparation delay and spacing of crowded messages.
  Positive gaps of one or two ticks are suppressed; the third nonadvancing
  scheduler call advances by three ticks. This intentionally changes fine timing.
- Append 1498 ticks, about two seconds, after the final output event. Source
  silence represented only by MIDI end-of-track does not extend this trailer.
- Use F3 only through 127 ticks, and F4 for larger delays. Write the legacy
  header fields and actual file lengths, without padding to a block boundary.
- Write continuous piano-pedal positions on channel 3 and generate Yamaha's
  binary companions on channel 1. Suppress repeated continuous positions.
  Preserve note velocities, other channel messages, and valid SysEx
  payloads. Unsupported standalone F7 escapes and unsafe packet sequences still
  fail before any destination is written.

The default `timing_policy="auto"` applies this behavior to fresh normal
Disklavier conversions throughout the app. It recognizes returning E-SEQ MIDI
through the APS conversion notice or archival header/timing metadata and keeps
that input on the existing preservation path. Removing all origin metadata
removes this automatic distinction. API callers can select `preserve` or
`mid2eseq` explicitly. Clavinova MDA continues to use its existing writer.

Pedal routing is a separate `pedal_policy="auto"` default for fresh Disklavier
MIDI. For each of CC64 and CC67 on channel 1, any intermediate value
1–126 identifies a continuous lane. Move the whole lane, including 0/127
endpoints, to channel 3, with binary companions generated as described below.
Binary-only lanes and other channels stay as supplied.
If channel 3 already contains that controller, preserve both lanes instead of
merging them. Note events on channel 3 reserve it for music and prevent new
pedal routing into it. Sostenuto CC66 stays on its source channel.

Yamaha's `hpdlCheck` compares each value with the previous raw value for that
controller, starting at zero:

| Controller | Generate channel-1 value 127 | Generate channel-1 value 0 |
| --- | --- | --- |
| CC64 sustain | Current ≥84, previous <84 | Current <82, previous ≥82 |
| CC67 soft | Current ≥64, previous <64 | Current <61, previous ≥61 |

This is an edge comparison, not a latched pedal state: sustain values 84, 83,
84 generate two presses with no intervening release. Channel-3 values remain
unchanged, but a repeated value is omitted. The detailed history also starts
at zero, so an initial zero is omitted. Each controller has independent state.

When both layers are emitted, the binary event comes first and the detailed
event has exactly the same tick. Conversion happens after the original source
event is scheduled. Added companions do not consume extra scheduler steps;
suppressed duplicates still retain their original place in the timing model.
Every retained source event and the end tick therefore keep the same timing
as the selected writer with pedal preservation enabled.

Recognized E-SEQ-origin MIDI and Clavinova MDA retain their routing by default.
API callers can select `pedal_policy="preserve"` for exact MID2ESEQ reproduction
or `pedal_policy="yamaha"` to apply routing to an E-SEQ-derived Disklavier input.
Explicit Yamaha routing rejects an MDA destination. Conversion reports identify
the affected controllers, generated companions, and suppressed duplicates.
They independently verify the complete expected event stream and timing;
missing, extra, reordered, or mistimed companions still report a mismatch.

Inspection of recovered Mark IV playback and conversion code confirms that
ordinary MIDI-to-E-SEQ conversion calls the pedal helper; it is not limited to
MX conversion. Native E-SEQ piano-part input bypasses it. Automatic Yamaha preparation also updates
the XG marker, detailed-pedal flag, and 16-bit note-channel mask to describe the
output. These header changes leave the legacy timing policy intact;
explicit `pedal_policy="preserve"` retains the original MID2ESEQ header too.

Title and filename changes modify only their requested fields. They preserve
the compatibility header's zero timing fields instead of recalculating them
during an unrelated edit. PIANODIR copies the resulting song header records.

## Validation and scope

With `pedal_policy="preserve"`, both supplied original MIDI/reference FIL pairs
remain byte-identical, including the public file conversion path, same-title
and same-filename edits, and copied catalog header records. Default Yamaha
preparation intentionally differs through its pedal layers and playback flags.
Reference music and album investigations remain outside version control.
Regression fixtures use small synthetic performances with independently
specified results.

An isolated executable linked only Yamaha's original `hpdl.o` to a bounded
stdin/stdout driver. All **75,536 event results** matched the new implementation,
including every previous/current pair for both controllers and 10,000
interleaved events. Checks included emitted bytes, ordering, suppression, and
the helper's half-pedal flag. No vendor code is distributed with the app.

The test suite covers both timing writers, dense simultaneous events, native
arrangements, occupied channels, round trips, delivered images, catalog flags,
and deliberately damaged conversion outputs. The preservation writer also
honors MIDI metadata length encodings, retains source precedence for simultaneous
meter changes, and keeps 4/4 until a later time signature actually occurs.
Archival returns retain real meter changes during opening silence. MIDI track
parsing stops at each end-of-track marker while retaining other tracks' later
events and their ending silence.

Yamaha's optional XP processing, selected left/right part remapping, and
volume/expression-to-velocity rendering are distinct transformations. They are
not automatically applied to otherwise preserved performances. The legacy
clock remains an intentional compatibility policy, not a claim to reproduce
every operation of Yamaha's full converter. Pedal software equivalence does
not establish physical piano response; the newly generated binary layer still
requires playback verification on hardware.
