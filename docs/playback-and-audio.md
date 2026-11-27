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

Audio preview and the Disklavier Audio Player follow the computer's current
default audio output when headphones connect, disconnect, or become the default.
They also refresh the output before playback. Switching outputs keeps the song's
position, volume, and playback state. Choose your headphones in the operating
system's sound settings if they are connected but are not the default output.

## Inspect Yamaha catalogs

**Utilities → File Inspection...** includes `PIANODIR.FIL`, `PDISK.MNG`, and
`PSONG.MNG` from the loaded disk or song folder. Select a catalog to see its structured view,
drop a catalog into the main list, or use **Open catalog...** to inspect a saved
catalog directly.

PDISK shows album information and header fields. PSONG shows the ordered song
list, MIDI filenames, titles, and individual record fields. PIANODIR shows the
album label and catalog number, ordered E-SEQ filenames and titles, declared
durations, arrangement types, write-protection flags, and record offsets.
Its inspection view is read-only. The raw-data view
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
unchanged until saving, including its raw and hexadecimal inspection views.
For compatible Yamaha MIDI destinations, Save also updates `PSONG.MNG` and
`PDISK.MNG`; exports create or update the catalogs beside the prepared songs.
ENSPIRE only carries existing catalogs or catalogs derived from `PIANODIR.FIL`;
it does not generate them for uncataloged songs.

PDISK and PSONG are supported companion files for MIDI collections. A conversion
to E-SEQ omits them from the prepared output; it does not delete the original
catalog files from the source folder.

## Play encoded Disklavier audio or a MIDI/audio pair

1. Open **Utilities → Disklavier Audio Player...**.
2. Open an encoded WAV or MP3, or a `.mid`/`.midi` file with a same-name WAV or MP3 in
   the same folder (for example, `Song.mid` and `Song.mp3`). You can also open
   the audio file to find its matching MIDI. Filename matching accepts
   differences in letter case. If both WAV and MP3 companions exist, opening
   the MIDI prefers WAV; open the MP3 directly to use it instead. Ambiguous
   matches within a format are reported instead of guessed.
3. The player detects the format and shows the selected pair, **Encoded WAV**,
   or **Encoded MP3**.
   Wait for preparation and piano rendering, then choose **Play**.
4. Listen to the piano alone or together with the accompaniment. The audio
   waveform below the piano roll shares its timeline and moving playhead.
   Click either view or use the seek bar, pause, and stop controls to check
   different passages.
5. With **Piano + accompaniment** selected, move the **Piano/Accompaniment
   balance** slider left for more piano or right for more accompaniment.
   The endpoints play only the selected part; the center mixes both equally.
   The player remembers the balance, independently of the master volume.
   Adjustments smoothly change the levels during playback without reloading
   the song. Switching to piano alone also keeps the same playback timeline.

Hover over **Open file...** for supported-file guidance. The empty piano roll
also has a tooltip; introductory instructions do not occupy the playback area.

For encoded audio, the player recovers the embedded MIDI and separates the
music from the control channel; the original MIDI, CD, and export manifest are
not required. Opening encoded audio uses its embedded performance even if a
same-name MIDI is present. Opening a MIDI uses that selected performance and
removes recoverable encoded control data from its accompaniment when necessary.

Ordinary pairs retain their MIDI tempo map, stereo accompaniment, and common
time zero. Pairing by name assumes the files are already synchronized; the
player does not guess a new offset or remove leading silence. The waveform
shows the accompaniment on the same timeline as the piano roll. Only the
software piano and clean accompaniment go to the speakers. Source files remain
unchanged.

Saving decoded MIDI requires Admin Mode for the current application session
and is available only for encoded audio playback. A pair already has its MIDI.
Admin Mode is not remembered after restarting the app.

The player accepts the app's 44.1-kHz, 16-bit stereo Y-modulation WAV exports,
and MIDI paired with 44.1-kHz, 16-bit mono or stereo PCM WAV or 44.1-kHz MP3.
MP3 playback uses LAME to prepare temporary PCM audio, respecting encoder delay
and padding metadata without adding a guessed MIDI offset. Missing LAME is
reported before preparation starts. Recoverable encoded MP3s are detected too;
damaged control data is reported instead of played as accompaniment.
It uses a software piano, with the built-in piano available when FluidSynth
or a SoundFont is unavailable. This is a listening tool;
it does not establish playback compatibility with a physical Disklavier.

## Decode files and folders for ENSPIRE

With Admin Mode enabled, open **Utilities → Decode Audio for ENSPIRE...**.
Add individual WAV/MP3 files or folders; folder selections include all subfolders.
Choose an output folder and start decoding. Existing MIDI or E-SEQ companions
do not prevent an encoded audio file from being inspected. Symbolic links are
not followed.

Each recovered song becomes a matching `.mid` and clean accompaniment `.mp3`
pair inside a new output directory. Selected folder names and their nested
structure are preserved. Conflicting names receive numbered suffixes; the new
run folder follows **Settings → Save Options → Add Random Suffixes to Output
Names**. Source files remain unchanged.

The decoder checks both stereo channels for Yamaha control data and retains
the shared MIDI/audio timing. Ordinary audio is skipped. Files with damaged or
ambiguous control data are reported individually, while the remaining files
continue. Lossy MP3 encoding can destroy control data, so recovery depends on
the source. LAME must be available before the batch can start.

MIDI clock, transport, and active-sensing messages are omitted from paired
exports without shifting musical events.
Unsupported system-common or reset messages are reported with their type and
time before a pair is saved.

Progress identifies scanning, decoding, and exporting. Cancellation keeps
completed pairs. The results list and JSON report record successful, skipped,
and failed files. Admin Mode is available only for the current app session.

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

<a id="smart-pianosoft-beta"></a>

## Smart PianoSoft: synchronized piano and music

The development build includes **Utilities → Smart PianoSoft...**.
After reading a confirmed Smart floppy or image, APS offers to prepare its
matching CD once capture-save and recovery dialogs have finished. Accepting
opens the utility with the loaded song list; declining leaves the disk
available for editing. Loading an extracted Smart MIDI folder opens the utility
automatically once active work and dialogs finish. Ripping starts only when
you choose the start button. Physical-piano playback remains unverified.

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

Set **Preparing for...** before opening the utility. The output is selected
from the current target each time, and can be changed for this album:

| Preparing for | Automatic output | Intended playback route |
| --- | --- | --- |
| ENSPIRE / DKC-900 | Paired MIDI + MP3 | Matching filenames together in one USB album folder |
| Mark IV / PRO | Encoded WAV | Mark IV encoded-audio playback |
| Mark III | Encoded WAV | External player into the appropriate piano/audio inputs |
| E3 / DKC-800 / DKC-850 | Encoded WAV | External player into the appropriate piano/audio inputs |
| Other or unspecified system | Encoded WAV, with an unverified-target note | Confirm that the controller has a compatible decoder |

For external-input playback on ENSPIRE, manually select **Encoded WAV**.
Direct ENSPIRE USB playback uses the paired-file option. Ordinary playback of
an encoded WAV can send the control signal to a speaker; choose the route
shown in the dialog.

### Prepare an album

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
2. Review **Preparing for** and **Output format**, select the CD drive and an
   output folder, then start CD ripping and export. APS snapshots the chosen
   source, securely reads the CD, verifies both synchronization fingerprints,
   and creates a new album directory with encoded WAVs or synchronized MIDI/MP3
   pairs. Loaded songs come from the existing session; the physical floppy is
   not read again. The source media are read-only.
3. For an existing music file, choose **Scan floppy**, select its MIDI row, and
   choose **Pair audio...** (or double-click the row). Pair every song and select
   **Use paired WAV/MP3 files** to work without a CD. WAV input must be stereo,
   16-bit, 44.1-kHz PCM. MP3 input must contain stereo, 44.1-kHz audio; LAME
   decodes it before synchronization, preserving encoder-delay and padding handling.
   The original MP3 and the decoded PCM used for matching are retained separately
   and recorded with checksums in the manifest.
   The start button identifies the selected export format when using paired files.

Track pairing and audio synchronization run automatically after starting; there
is no manual “verify by audio” step. When the MIDI contains Yamaha's CD reference,
the dialog shows **CD track N (disk reference)**. Otherwise it shows
**Match CD track automatically**; floppy song numbers need not match CD track numbers.
The song table uses the available height, while
progress and details appear when needed. The main controls stay compact;
smaller windows or larger fonts can still scroll to keep every control reachable.
During CD ripping, the bar measures audio read across all audio tracks. The label
shows the actual CD track number, its position in the audio-track list, and
whether APS is reading, checking the saved WAV or disc, saving, or calculating
the audio checksum. Matching and playback-file export have their own progress stages.

If a CD read fails or the drive disconnects, APS stops the reader and shows how
to retry. Reconnect the drive, insert the matching CD, then use **Refresh** and
select the drive again before restarting. The scanned floppy and manual pairings
remain available. Each retry reads the CD into a new output folder; verified
files from the failed attempt remain in its folder with a failed manifest.

To use names such as `Smart PianoSoft - BELIEVE - CHER`, turn off
**Settings → Save Options → Add Random Suffixes to Output Names**. This global
preference also applies to Mark IV backups, extraction progress files, and logical
recovery reports. Existing outputs are preserved: later outputs with the same
name receive `(2)`, `(3)`, and so on. The setting applies to newly created outputs.

When using the current song list, the utility includes its remaining songs and
staged MIDI and title edits. Sorting the list or removing songs preserves each
song's original catalog position; audio matching determines its CD track.
The pairing table displays the current filenames. Preserved source MIDI copies
retain their original catalog filenames. For paired playback on a compatible
Yamaha destination, the playback folder uses short matching names such as
`01.mid` and `01.mp3`, with the current song titles in `PSONG.MNG` and album title
in `PDISK.MNG`. The manifest includes the generated catalogs’ checksums.

Direct USB-floppy and physical CD reading in this dialog require Linux;
Greaseweazle uses the app's regular floppy-reading backend. Secure CD reading
uses `cdparanoia`, bundled by default in Linux AppImages; source installations
need the `cdparanoia` package. Windows can use Greaseweazle, saved floppy
images/folders, or an already loaded song list with paired WAV/MP3 files.
Pre-emphasized CDs are rejected because
automatic de-emphasis has not yet been implemented.

### Use the output

Each run creates a new album directory containing:

- **MIDI:** the source snapshot and catalogs. These files retain the original
  performance timing; they are not the synchronized ENSPIRE export.
- **WAV:** unchanged CD PCM, paired WAV copies, or PCM decoded from paired MP3s.
- **MP3**, when using MP3 input: unchanged copies of the paired source MP3s.
- **ENSPIRE**, for paired export: synchronized `.mid` and clean `.mp3` files
  with identical basenames. Copy this folder to USB as an album, optionally
  rename the folder, and keep both files for each song together.
- **Disklavier**, for encoded export: stereo WAVs with mono music on the left
  and Yamaha piano-control data on the right.
- **manifest.json:** completion status, pairing evidence, file hashes, timing
  offsets, speed ratios, and the selected output format.

For example, `01 - Song.mid` and `01 - Song.mp3` are one paired song. Selecting
that song through ENSPIRE's **My Songs** uses the associated files. The paired
MIDI has the computed timing correction written into every musical event,
including note releases, pedals, controllers, and musical SysEx. A common
lead-in preserves events that would otherwise occur before the recording
starts. The clean source's stereo channels are retained. CD synchronization
and control messages are removed from the playback MIDI.

The floppy can omit songs that are present on the CD. Yamaha's MIDI metadata
can contain the CD's track-start table and the intended song's position in that
table. This identifies the CD track independently of the floppy catalog order.
APS tries that reference first, but compares each song's two audio fingerprints
with every CD track and requires one unique match. For
example, four floppy songs can match CD tracks **1, 3, 4, and 5**, skipping CD
track 2. The skipped recording remains in **WAV**, but has no prepared piano
file. Prepared songs keep their floppy catalog numbers; **manifest.json**
records the actual CD audio chosen for each one.

`PDISK.MNG` supplies the album title; `PSONG.MNG` supplies song identities and
catalog order. The inspected PDISK catalogs do not provide per-song CD timing
corrections. Yamaha's [Mark III operating manual](https://fr.yamaha.com/files/download/other_assets/3/314153/DKVMK3Full_A_e_X2657B0.pdf)
(page 61) explains that different editions of the same CD can have different
start times and tempos, and that Smart PianoSoft adjusts playback for these
differences. The app therefore uses the embedded CD reference (or catalog order
when no valid reference is present) as a hint and automatically checks the
MIDI's audio references against the actual CD to calculate alignment.
These catalogs also occur on ordinary Yamaha MIDI disks, so the
automatic offer additionally requires both synchronization fingerprints in
every loaded song. Neither the CD reference nor catalog order proves a match.
Both beginning and ending fingerprints must match unambiguously. They establish the start offset and playback
speed correction; the source MIDI's complete tempo map is retained. Incorrect,
silent, ambiguous, or unsupported references stop the operation. All pairings
are verified before encoding begins. Failed checks and interrupted runs without
completed audio leave no output folder. If cancellation or a read error leaves
useful progress, the dialog asks whether to **Save incomplete progress** or
**Discard** it, including when closing the window. Saving keeps the files and an
explicitly incomplete report; discarding removes only that run's output folder.
The scanned MIDI stays available for retries while the dialog remains open.
Partial encoded WAVs or MIDI/MP3 pairs are not published as completed songs.
Saved WAVs can be paired on a retry; recovery locations are reported if needed.

Paired export requires the packaged or installed LAME encoder and uses tagged
192 kbps MP3. LAME delay/padding metadata is retained. APS does not add a guessed
500 ms mechanical shift or a universal MP3-delay correction. The actual
acoustic result and the piano's handling of MP3 timing metadata still need a
playback check; the manifest records `hardware_verified: false`.

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

The [APS Smart PianoSoft conversion guide](https://www.alexanderpeppe.com/converting-smart-pianosoft-floppy-disks/)
demonstrates same-basename MIDI/MP3 playback on ENSPIRE. Yamaha documents
supported audio/MIDI formats and route-specific delay settings in the
[ENSPIRE controller manual](https://data.yamaha.com/files/download/other_assets/4/1358314/enspire_controller_st_pro_en_om_f0.pdf).
These do not establish a universal latency correction for the generated files.

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

Yamaha selects the strongest correlation within each fingerprint's search
window. Similar repetitions of an intro do not require a large score gap.
APS follows that selection rule while keeping a stricter minimum match score,
checking both anchors, and rejecting inconsistent playback-speed corrections.
It also rejects competing peaks when their apparent difference can be explained
by the interpolation corrections used to estimate positions between samples.
That last check is an APS safeguard; it is not a Yamaha fingerprint-format rule.

For verification:

1. Let export verify both fingerprints. The album's `manifest.json` records
   each pairing's `offset_seconds`, `time_scale`, and match scores under
   `alignment`. A constant offset fixes a displaced start; a speed ratio fixes
   a difference that accumulates toward the end. An uncertain match stops export.
2. For encoded output, open the finished WAV in **Disklavier Audio Player** and listen to
   **Piano + accompaniment** near the beginning, middle, and end. This checks
   the piano data actually recovered from the exported waveform. The software
   regression suite also compares decoded note times against a known alignment
   across the song; export does not automatically decode every completed file.
3. For paired output, keep the synchronized `.mid` and `.mp3` together and
   check the song on the intended controller. Synthetic tests also use real
   LAME round trips to check music alignment independently of a piano.
4. To verify a physical Disklavier, record its acoustic piano and accompaniment
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
