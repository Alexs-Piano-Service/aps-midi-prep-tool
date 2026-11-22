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

Audio preview and the Disklavier WAV Player follow the computer's current
default audio output when headphones connect, disconnect, or become the default.
They also refresh the output before playback. Switching outputs keeps the song's
position, volume, and playback state. Choose your headphones in the operating
system's sound settings if they are connected but are not the default output.

## Inspect Yamaha catalogs

**Utilities → File Inspection...** includes `PDISK.MNG` and `PSONG.MNG` from
the loaded disk or song folder. Select a catalog to see its structured view,
drop a catalog into the main list, or use **Open catalog...** to inspect a saved
catalog directly.

PDISK shows album information and header fields. PSONG shows the ordered song
list, MIDI filenames, titles, and individual record fields. The raw-data view
keeps the original contents available for closer inspection. Undocumented
fields are displayed as stored, without assigning speculative meanings.
Use **Apply album title** in PDISK to populate the current album name. Use
**Apply song titles** in PSONG to stage title changes in the loaded MIDI files.
Songs are matched by filename, including recognized extraction filenames,
not by their current table order. Unmatched or ambiguous records are reported
and skipped. Load the MIDI folder first when applying a separately opened
PSONG catalog.

When MIDI files and their catalogs are dropped together, the album and song
titles are applied automatically. Catalogs from separate folders are matched
to their own songs where possible; conflicting album names require choosing
one catalog explicitly. Use **Save** to write staged song-title changes, or
**Undo** to revert them. Applying metadata leaves the catalog's original bytes
unchanged, including its raw and hexadecimal inspection views.

PDISK and PSONG are supported companion files for MIDI collections. A conversion
to E-SEQ omits them from the prepared output; it does not delete the original
catalog files from the source folder.

## Play a completed Disklavier WAV

1. Open **Utilities → Disklavier WAV Player...**.
2. Open a completed encoded WAV from Smart PianoSoft export.
3. Wait for decoding and piano rendering, then choose **Play**.
4. Listen to the piano alone or together with the accompaniment. The audio
   waveform below the piano roll shares its timeline and moving playhead.
   Click either view or use the seek bar, pause, and stop controls to check
   different passages.
5. With **Piano + accompaniment** selected, move the **Piano/Accompaniment
   balance** slider left for more piano or right for more accompaniment.
   The endpoints play only the selected part; the center mixes both equally.
   The player remembers the balance, independently of the master volume.
   Adjustments prepare in the background and retain your playback position
   and the shared timeline.

This separate utility recovers MIDI directly from the WAV's right-channel
control signal; the original MIDI, CD, and export manifest are not required.
It preserves the shared timing of the piano and left-channel accompaniment.
The waveform shows that accompaniment, including its original silence and
lead-in, rather than the synthesized piano or encoded control signal.
Only decoded piano sound and accompaniment go to the speakers.
The source WAV remains unchanged. Saving decoded MIDI is hidden and unavailable
in normal use; it requires Admin Mode for the current application session.
Admin Mode is not remembered after restarting the app.

The player accepts the app's 44.1-kHz, 16-bit stereo Y-modulation WAV exports.
It uses a software piano, with the built-in piano available when FluidSynth
or a SoundFont is unavailable. This is a listening tool for encoded exports;
it does not establish playback compatibility with a physical Disklavier.

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

## Smart PianoSoft (Beta)

The next version restores **Utilities → Smart PianoSoft (Beta)...**. After
reading a clearly Smart floppy or floppy image, the app asks whether to rip
the matching audio CD. Accepting opens the dialog with the loaded song list;
ripping starts only when you choose **Start CD rip + WAV export**. Declining leaves the
loaded disk available for editing.

When protected Yamaha or confirmed Smart media is detected, a personal-use
dialog links to the disclaimer. Choose **Agree** to continue reading or
**Cancel** to stop. **Never show this again** is checked by default; accepting
with it checked remembers your agreement across launches, including after
**Show Dismissed Messages Again**. Uncheck it to be asked for the next source.
Cancelling does not remember an agreement. Seeing the older notice alone does
not suppress this agreement dialog.

The dialog pairs Yamaha Smart PianoSoft floppies supplied for use
with commercial audio CDs. It requires
`PSONG.MNG`, its MIDI files, and the two Yamaha synchronization fingerprints
inside each MIDI. An ordinary PianoSoft solo disk or a MIDI folder with a
generated catalog does not qualify.

To prepare an album:

1. Load the Smart PianoSoft disk with **Read Floppy...** or **Open Image...**,
   then accept the CD offer or open the utility. It automatically uses the loaded songs and their
   catalog track order, song titles, and album title, without reading the
   physical floppy again. Insert the matching audio CD. To use another source,
   select **Choose source...** and choose **Floppy drive (USB or Greaseweazle)**,
   an IMG/HFE image, or an extracted floppy folder. A Greaseweazle source lets
   you select the drive, disk format, read revolutions, and retry count. With no
   loaded Smart PianoSoft source, leaving the source blank discovers USB and
   Greaseweazle drives and offers a choice when more than one is available.
   Previously extracted
   `01 - Song title.mid` filenames are supported when their original catalog is
   retained.
2. Select the CD drive and an output folder, then choose **Start CD rip + WAV export**.
   The app snapshots the chosen source, securely reads the CD, verifies
   the recording, and creates a new album directory with **one combined WAV
   per song**. Loaded songs come from the
   existing session; the physical floppy is not read again. The source media are
   read-only.
3. For an existing music file, choose **Scan floppy**, select its MIDI row, and
   choose **Pair WAV...** (or double-click the row). Pair every song and select
   **Use paired WAV files** to work without a CD. The implementation accepts
   stereo, 16-bit, 44.1-kHz PCM WAV files; other audio formats need conversion first.
   The start button changes to **Start WAV export** when using paired files.

Track pairing and audio synchronization run automatically after starting; there
is no manual “verify by audio” step. The dialog shows the catalog's suggested
CD track before processing. The song table uses the available height, while
progress and details appear when needed. The main controls stay compact;
smaller windows or larger fonts can still scroll to keep every control reachable.
During CD ripping, the bar measures audio read across all audio tracks. The label
shows the actual CD track number, its position in the audio-track list, and
whether APS is reading, checking the saved WAV or disc, saving, or calculating
the audio checksum. Matching and WAV encoding have their own progress stages.

To use names such as `Smart PianoSoft - BELIEVE - CHER`, turn off
**Settings → Save Options → Add Random Suffixes to Output Names**. This global
preference also applies to Mark IV backups, extraction progress files, and logical
recovery reports. Existing outputs are preserved: later outputs with the same
name receive `(2)`, `(3)`, and so on. The setting applies to newly created outputs.

When using the current song list, the utility includes its remaining songs and
staged MIDI and title edits. Sorting the list or removing songs preserves each
song's original CD track number. The pairing table displays the current
filenames; the exported MIDI files retain the filenames from the Smart PianoSoft
catalog so the catalog continues to identify them correctly.

Direct USB-floppy and physical CD reading in this dialog require Linux;
Greaseweazle uses the app's regular floppy-reading backend. Secure CD reading
uses `cdparanoia`, bundled by default in Linux AppImages; source installations
need the `cdparanoia` package. Windows can use Greaseweazle, saved floppy
images/folders, or an already loaded song list with paired WAV files.
Pre-emphasized CDs are rejected because
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

`PDISK.MNG` supplies the album title; `PSONG.MNG` supplies song identities and
catalog order. The inspected PDISK catalogs do not provide per-song CD timing
corrections. Yamaha's [Mark III operating manual](https://fr.yamaha.com/files/download/other_assets/3/314153/DKVMK3Full_A_e_X2657B0.pdf)
(page 61) explains that different editions of the same CD can have different
start times and tempos, and that Smart PianoSoft adjusts playback for these
differences. The app therefore uses catalog order first and automatically checks
the MIDI's audio references against the actual CD to calculate alignment.
These catalogs also occur on ordinary Yamaha MIDI disks, so the
automatic offer additionally requires both synchronization fingerprints in
every loaded song. Catalog order is a candidate pairing, not proof of a match. Both beginning and
ending fingerprints must match. They establish the start offset and playback
speed correction; the source MIDI's complete tempo map is retained. Incorrect,
silent, ambiguous, or unsupported references stop the operation. All pairings
are verified before encoding begins. Cancellation or a read error leaves an
explicitly incomplete report and any verified originals available for recovery;
partially encoded WAVs are not published. Retained WAVs can be paired on a retry.

The encoded files use Yamaha PianoSoft PlusAudio/Y-modulation, intended for
CD-DA playback or a correctly configured analog MIDI/audio input. Static Mark IV
inspection also identifies a standalone encoded-WAV playback route; playback of
APS exports on physical hardware remains unverified. The right channel contains
control data, not listening audio.
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

### Check Smart PianoSoft timing

Starting the MIDI and CD at the same instant is sufficient only when their
track starts and playback rates already match. The app automatically matches
the beginning and ending audio fingerprints stored in each Smart MIDI against
the actual CD rip. It then adjusts MIDI event times using the measured start
offset and speed ratio, preserving the performance's original tempo changes.
PDISK and PSONG supply catalog information, not these timing measurements.

The supplied Mark IV drive confirms that Yamaha also measures two fingerprint
offsets. Its controller saves the calibration against both the MIDI and CD
identities, then follows the audio clock with a correction that changes between
the two anchors. The inspected host sequencer does not continuously rematch
the audio or fit independent timing corrections throughout the song. APS uses
a direct fit between the same two anchors; it does not reproduce every detail
of the older sequencer's integer arithmetic.

For verification:

1. Let export verify both fingerprints. The album's `manifest.json` records
   each pairing's `offset_seconds`, `time_scale`, and match scores under
   `alignment`. A constant offset fixes a displaced start; a speed ratio fixes
   a difference that accumulates toward the end. An uncertain match stops export.
2. Open the finished encoded WAV in **Disklavier WAV Player** and listen to
   **Piano + accompaniment** near the beginning, middle, and end. This checks
   the piano data actually recovered from the exported waveform. The software
   regression suite also compares decoded note times against a known alignment
   across the song; export does not automatically decode every completed file.
3. To verify a physical Disklavier, record its acoustic piano and accompaniment
   together on one recorder. This checks the additional hardware, speaker, and
   mechanical timing that a software piano cannot establish.

The common lead-in in an exported WAV moves piano and accompaniment together;
it does not alter their relative timing. The Mark IV has separate, route-specific
audio and MIDI delay mechanisms. Do not add a universal half-second MIDI shift
to compensate for them. Likewise, a listening device's delay affects both parts
of the player's single mixed audio stream together.

The export follows the [MID2PianoCD documentation](https://www.kinura.net/mid2pianocd/)
for combining a MIDI performance and mono/stereo 16-bit accompaniment in one
encoded file per song. Its notes also confirm that Smart playback SysEx must
be removed when producing Disklavier audio and that serialized piano data has
limited bandwidth. APS retains the source performance velocities and verifies
CD alignment before encoding; it does not invoke or redistribute MID2PianoCD.

On November 15, 2026, a read-only inspection of the supplied Mark IV drive found
the root partition's `/usr/local` link points to `/home/local`. The mounted
`/var` partition's `dm4_4.26build1.zip` update archive contains
`home/local/sbin/seq` with SHA-256
`aea767ebb4bcf4bd15419e60b44642c5935e1b053d614eb4f76687afe30934a7`.
This exactly matches the privately retained sequencer reference used in the
previous synchronization/codec investigation. Its symbols include
`seqSyncIsSysEx`, `Start_Envelope`, `Start_Correlate`, and the `DemodY` decoder.
The retained evidence establishes the 6,300 Hz carrier, 3,150 symbols/second,
differential phase mapping and quasi-MIDI representation used by the existing
encoder. No vendor program was executed during this restoration, and no
proprietary binary or song was added to the application.

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
