# Listen to songs and create audio copies

[All user guides](README.md) · [Getting started](getting-started.md)

## Preview a song

1. Load songs using **Open MIDI Folder** or **Open Image**.
2. Open **Utilities → File Inspection...** and select a song in its list.
3. Set **Playback output** to **Audio preview** for your computer's speakers,
   or select a connected MIDI device. Use **Refresh** after connecting a device.
4. Choose **Play**. You can mute channels or adjust the preview mix and tempo
   without changing the saved song.

The piano roll shows notes, channels, instruments, tempo, and pedals. Basic
piano preview works without a SoundFont. A SoundFont supplies instrument sounds
for richer playback; use **Download SoundFonts...** to add one. SoundFont
playback requires FluidSynth. See [optional tools](../README.md#compatibility-and-optional-tools).

The first audio preview renders the song; subsequent Play clicks reuse that
audio. Completed previews are cached across inspection sessions, with automatic
replacement when the song, preview mix, SoundFont, or renderer changes.

## Create WAV or MP3 copies

1. Load the songs you want to render, then open **Utilities → Render Audio...**.
   This utility renders all currently listed MIDI or E-SEQ songs.
2. Select a SoundFont, or choose **Download SoundFonts...** to add one.
3. Choose **WAV** or **MP3** and an output folder.
4. Choose **Render** and check the completion result.

SoundFont rendering requires FluidSynth and a SoundFont; MP3 also requires
LAME. The dialog explains missing requirements. Basic piano preview does not
require FluidSynth, but it is not the SoundFont renderer used by this utility.
See [optional tools](../README.md#compatibility-and-optional-tools).

## Smart PianoSoft development (deferred)

**The Smart PianoSoft synchronization utility is deferred and is not available
from the menus or shortcuts in version 0.8.8.** Its implementation, tests, and
research remain in the repository for future development. The workflow below
describes that retained implementation, not a supported 0.8.8 command.

The development dialog pairs Yamaha Smart PianoSoft floppies supplied for use
with commercial audio CDs. It requires
`PSONG.MNG`, its MIDI files, and the two Yamaha synchronization fingerprints
inside each MIDI. An ordinary PianoSoft solo disk or a MIDI folder with a
generated catalog does not qualify.

When the retained dialog is invoked by a development harness:

1. Load the Smart PianoSoft disk with **Read Floppy...** or **Open Image...**,
   then open the utility. It automatically uses the loaded songs and their
   catalog track order, song titles, and album title, without reading the
   physical floppy again. Insert the matching audio CD. To use another source,
   select **Choose source...** and choose a USB floppy, an IMG/HFE image, or an
   extracted floppy folder. With no loaded Smart PianoSoft source, leaving the
   source blank detects a USB floppy. Previously extracted
   `01 - Song title.mid` filenames are supported when their original catalog is
   retained.
2. Select the CD drive and an output folder, then choose **Create album**.
   The app snapshots the chosen source, securely reads the CD, verifies
   the recording, and creates a new album directory. Loaded songs come from the
   existing session; the physical floppy is not read again. The source media are
   read-only.
3. For an existing music file, choose **Scan floppy**, select its MIDI row, and
   choose **Pair WAV...** (or double-click the row). Pair every song and select
   **Use paired WAV files** to work without a CD. The implementation accepts
   stereo, 16-bit, 44.1-kHz PCM WAV files; other audio formats need conversion first.

When using the current song list, the utility includes its remaining songs and
staged MIDI and title edits. Sorting the list or removing songs preserves each
song's original CD track number. The pairing table displays the current
filenames; the exported MIDI files retain the filenames from the Smart PianoSoft
catalog so the catalog continues to identify them correctly.

Physical CD and floppy reading is implemented for Linux. Secure CD reading
uses `cdparanoia`, which is not bundled in the default 0.8.8 package. Development
builds can include it with `BUNDLE_CDDA=1`; source tests of CD reading need the
`cdparanoia` package. The retained Windows implementation can use saved floppy
images/folders and paired WAV files. Pre-emphasized CDs are rejected because
automatic de-emphasis has not yet been implemented.

The result contains:

- **MIDI:** the loaded song snapshot, with catalogs reflecting its selected songs
  and titles. For a source chosen directly in the utility, MIDI bytes and MNG
  catalogs are preserved unchanged. Original files and source media are never
  modified.
- **WAV:** unchanged CD PCM or paired WAV files.
- **Disklavier:** encoded stereo PCM WAVs with music mixed to the left channel
  and Yamaha piano-control data on the right.
- **manifest.json:** completion status, pairings, source/output hashes,
  synchronization offsets, speed ratios, and correlation scores.

Catalog order is a candidate pairing, not proof of a match. Both beginning and
ending fingerprints must match. They establish the start offset and playback
speed correction; the source MIDI's complete tempo map is retained. Incorrect,
silent, ambiguous, or unsupported references stop the operation. All pairings
are verified before encoding begins. Cancellation or a read error leaves an
explicitly incomplete report and any verified originals available for recovery;
partially encoded WAVs are not published. Retained WAVs can be paired on a retry.

The encoded files use Yamaha PianoSoft PlusAudio/Y-modulation, intended for
CD-DA playback or a correctly configured analog MIDI/audio input. **Ordinary
WAV import from USB or a hard disk has not been verified as a piano-control
playback route.** The right channel contains control data, not listening audio.
The output gives audio and MIDI a common lead-in for signal acquisition;
the piano's PlusAudio playback path supplies its own mechanical delay handling.
Physical piano playback, mechanical timing, and other Disklavier generations
remain future acceptance work; the generated manifest records
`hardware_verified: false`.

The implementation was checked against the Mark IV sequencer's fingerprint
filtering and its independent MIDI/audio decoder. Synthetic tests cover the
control alphabet, MIDI messages, offset/drift recovery, and cancellation;
privately held CD/MIDI material is used only for local validation and is not
distributed. Yamaha describes fingerprint-based timing correction in
[US7863513B2](https://patents.google.com/patent/US7863513B2/en), and Y-modulation
in [US20030061931A1](https://patents.google.com/patent/US20030061931A1/en).
The [Mark IV advanced manual](https://usa.yamaha.com/files/download/other_assets/3/321663/DKVMK4_B_e_X6398K0_02.pdf)
describes analog MIDI input and MIDI input delay settings. No Yamaha software
or commercial music is bundled with this feature.

## Inspect E-SEQ details

For E-SEQ songs, **File details** includes the original header's startup tempo,
tempo factors, meter, pedal and channel flags, write protection, and display
mode. Raw values accompany their meanings, and uncertain fields remain marked
as uninterpreted. The decoded MIDI preview appears below the source details.

## Convert tracks or merge piano parts

File Inspection also has **Convert to Type 0** and **Merge Channels to Piano**
buttons. Type 0 conversion accepts MIDI; piano merging accepts MIDI and E-SEQ.
Each stages an edit and refreshes the preview. Type 0 combines tracks
while keeping channels and instruments; merging routes note parts to MIDI
channel 1 and selects Acoustic Grand Piano while
keeping the source format. E-SEQ edits preserve native timing, titles, song order,
and Yamaha's separate pedal-detail data. These edits use the complete song,
including hidden or muted preview channels. Use **Save** to write them or
**Edit → Undo** to revert.

### Choose how overlapping notes are handled

When a piano merge finds overlapping notes on the same key, it asks how to
handle them. **Smart repair** removes long notes covering multiple shorter
strikes, keeps the shortest simultaneous note, and trims remaining overlaps.
**Keep attacks** keeps the longest simultaneous note and trims overlaps at the
next attack. Both combine duplicate unisons and use exact tick boundaries,
without moving attacks or resuming discarded tails. **Merge only** keeps the
existing overlapping-note behavior. Chords on different keys, notes routed to
different MIDI ports, and notes that only touch do not prompt. Repairs preserve
each note's MIDI destination, including generated releases. The same choice
applies when combining instruments as piano during Type 0 conversion.

Smart repair can also remove a legitimate sustained note beneath repeated
strikes, including its leading and trailing portions. Destination preparation
does not run overlap repair, even with a remembered Smart preference.
When an explicit merge stages a repair, **Overlap repair: Smart** or
**Overlap repair: Keep attacks** stays visible beside the preparation state;
hover over it to see affected filenames. The indicator clears when the staged
repair is undone, discarded, or committed with **Save**.

Check **Use this behavior for all future channel merges** to remember the choice
across files and app restarts. Without that check, each affected song asks again.
Open **Settings → Overlapping Piano Notes...** any time to change the behavior,
or uncheck the box and save to restore the prompt for each affected song.
**Settings → Show Dismissed Messages Again** also restores the prompt for future overlaps.

**Edit → Merge Channels to Piano...** applies the same merge to one song or
all listed MIDI and E-SEQ songs, including files being edited inside a disk image.

### Advanced: immediate-mute messages when merging

Channel merging and Type 0 piano remapping translate All Notes Off (CC123) and
the related mode commands (CC124–127) into note releases for their source parts.
All Sound Off (CC120) instead requires an immediate mute, including sustained
notes and release tails. APS retains CC120 when no other source part could still
be sounding. Otherwise, it uses note releases as a lossy approximation and flags
the removed CC120 in Conversion details; sustain or release tails may continue.
Because MIDI does not specify a receiver's release duration, a part remains
potentially sounding after its note-offs until a retained immediate mute or
recognized system reset. Keep the original channels (including ordinary Type 0
conversion without piano remapping) when exact source-specific CC120 behavior
is required. See the [MIDI message definitions](https://midi.org/summary-of-midi-1-0-messages).

[![MIDI notes, channels, tempo, and pedals in File Inspection](images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)](images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)

[All user guides](README.md) · [Review conversion reports](preparation-reliability.md#understand-conversion-reports)
