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

## Smart PianoSoft: synchronized piano and music

The development build includes **Utilities → Smart PianoSoft...**. Loading a
Smart PianoSoft disk or image opens the utility automatically after the source
is ready. APS recognizes the actual synchronization fingerprints as well as
`PSONG.MNG`; an ordinary MIDI folder with a generated catalog does not trigger it.
This workflow remains a beta and has not been accepted on physical pianos.

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

1. Load the original Smart PianoSoft disk with **Read Floppy...** or
   **Open Image...**. The utility uses the loaded songs, catalog order, song
   titles, and album title without reading the physical floppy again. You can
   also open the utility from **Utilities**, or choose another floppy, image,
   or extracted folder with **Choose source...**.
2. Review **Preparing for** and **Output format**, insert the matching CD,
   and choose the CD drive and an output folder.
3. Choose **Create album**. APS snapshots the selected MIDI, securely reads
   the CD, checks both audio fingerprints for each song, and calculates the
   start offset and speed correction before creating playback files.
4. For previously ripped audio, choose **Scan floppy**, select each song,
   and choose **Pair WAV...** (or double-click its row). Pair every song and
   select **Use paired WAV files**. Inputs must be stereo, 16-bit, 44.1 kHz
   PCM WAV.

The current-list snapshot includes remaining songs and staged MIDI/title edits.
Sorting or removing rows preserves each song's original catalog track number.
Previously extracted `01 - Song title.mid` filenames work when the original
catalog is retained. A source without the two synchronization fingerprints
cannot be synchronized by this tool.

Physical CD and floppy reading is implemented for Linux. Secure CD reading
requires `cdparanoia`; development AppImages can bundle it with `BUNDLE_CDDA=1`.
On Windows, use saved images/folders and paired WAV files. Pre-emphasized CDs
are rejected because automatic de-emphasis is not implemented.

### Use the output

Each run creates a new album directory containing:

- **MIDI:** the source snapshot and catalogs. These files retain the original
  performance timing; they are not the synchronized ENSPIRE export.
- **WAV:** unchanged CD PCM or paired WAV copies.
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

Both beginning and ending fingerprints must match. Catalog order and manually
selected audio are candidate pairings, not proof of a match. Incorrect,
silent, ambiguous, or unsupported recordings stop preparation. All pairings
are checked before playback files are generated. Cancellation or failure leaves
an incomplete report and retained source copies; a partial MIDI/MP3 pair is not
presented as a completed song. Recovery locations are reported if needed.

Paired export requires the packaged or installed LAME encoder and uses tagged
192 kbps MP3. LAME delay/padding metadata is retained. APS does not add a guessed
500 ms mechanical shift or a universal MP3-delay correction. The actual
acoustic result and the piano's handling of MP3 timing metadata still need a
playback check; the manifest records `hardware_verified: false`.

### Sources and verification

The [APS Smart PianoSoft conversion guide](https://www.alexanderpeppe.com/converting-smart-pianosoft-floppy-disks/)
demonstrates same-basename MIDI/MP3 playback on ENSPIRE. Yamaha documents
supported audio/MIDI formats and route-specific delay settings in the
[ENSPIRE controller manual](https://data.yamaha.com/files/download/other_assets/4/1358314/enspire_controller_st_pro_en_om_f0.pdf).
These do not establish a universal latency correction for the generated files.

The fingerprint implementation was checked against the Mark IV sequencer's
filtering. Original synthetic tests cover offset and drift recovery, tempo-map
handling, MIDI messages, channel routing, and cancellation. Real LAME round trips
check music alignment independently of a physical piano. Private customer music
is never included in the repository's test fixtures.

Yamaha describes fingerprint timing correction in
[US7863513B2](https://patents.google.com/patent/US7863513B2/en) and Y-modulation in
[US20030061931A1](https://patents.google.com/patent/US20030061931A1/en).
