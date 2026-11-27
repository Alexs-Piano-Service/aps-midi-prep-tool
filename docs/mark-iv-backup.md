# Back up and prepare a Mark IV music library

[All user guides](README.md) · [Getting started](getting-started.md)

Use **Utilities → Back Up Mark IV Music...** to back up a Disklavier Mark IV
music drive or prepare playback files from an existing APS Mark IV backup.
Choose paired MIDI + MP3 for ENSPIRE USB playback or encoded WAV for a compatible
audio playback route. APS can use separately stored MIDI and accompaniment,
including Smart PianoSoft songs, as well as recoverable Yamaha-encoded audio.

A drive backup copies and verifies the originals into named album folders.
Preparing an existing backup reads those originals and writes a separate
playback folder, without copying the entire archive again. An optional step
also converts supported legacy E-SEQ songs to MIDI.

## Before you start

- For a music drive or a copy of its data partition, select the folder
  containing **`songs/`**, rather than `songs/` itself.
- For an existing APS backup, select its **`MarkIV-Backup-...`** folder
  containing **`manifest.json`**. Keep its album folders and
  **`_metadata/original/`** together so the saved catalog can identify
  separately stored performances and accompaniment.
- Use an offline data volume or a stable copy while scanning and backing up.
  APS does not connect to a running piano database.
- Choose a destination outside the source. For a mounted music drive, use a
  separate destination filesystem with room for the backup and playback files.

Mounted-drive discovery is available on Linux and Windows. APS does not mount
partitions or install filesystem drivers; the operating system must already
be able to read the source. A copied data folder works on either platform.
APS opens source files only for reading. A read-only mount can also enforce
that at the operating-system level.

## Choose and scan the source

1. Open **Utilities → Back Up Mark IV Music...**.
2. Under **Music drive or backup folder**, choose a drive or use **Browse...**
   to select a data folder or existing backup. **Refresh drives** updates the
   list of mounted drives.
3. Choose **Scan music**. Review the album names, collection, file counts, and
   sizes. Select an album to see its track list, file types, sizes, and available
   album information in **Details**. Check **View report...** for scan warnings
   or errors before continuing.

Double-click an album to open a larger view with track metadata, source and
backup paths, and supporting files. Drag the divider above **Details** to
resize it relative to the album list. Selecting an album shows its details;
the operation uses the scanned library.

## Choose the playback format and start

| Playback format | Result |
| --- | --- |
| **Paired MIDI + MP3 (ENSPIRE USB)** | Piano performance and clean accompaniment with matching filenames in one album folder. |
| **Encoded WAV (music + piano control)** | Music and Yamaha control audio in one stereo WAV. The format tooltip describes the route for the selected **Preparing for** target. |

1. Select an **Output destination** outside the source. For a mounted music
   drive, use a separate destination filesystem.
2. Choose **Playback format**. ENSPIRE / DKC-900 selects paired MIDI + MP3 by
   default; other targets select
   encoded WAV. The default is refreshed whenever the utility opens, and you
   can change it for this operation.
3. Optionally select **Also convert legacy E-SEQ files to MIDI**. This is off
   by default, and APS remembers the choice. For a drive backup,
   **Keep the verified originals and save MIDI copies alongside them** becomes
   available when conversion is on and is checked by default. Uncheck it to
   keep only the MIDI version of successfully converted E-SEQ songs in the
   new backup. Preparing an existing backup always leaves its originals in
   place, so that retention control is hidden.
4. Choose **Back up music** for a drive/data folder, or **Prepare playback files**
   after scanning an existing backup. Progress covers verification and playback
   preparation, plus copying when making a new archive. Check the result and
   **View report...** for unavailable companions or other failures. Use
   **Open output folder** to view the files.

Drive backups create a new, uniquely named `MarkIV-Backup-...` folder in the
destination. Existing backups are kept. By default, its album folders contain
original-format music with unchanged file contents, including audio and
supporting assets. Portable filenames and suffixes handle names that would
otherwise collide.

Preparing an existing backup creates **`MarkIV-Prepared-...`** instead. It
contains playback output, optional E-SEQ-to-MIDI copies, and a manifest recording
the verified source files. It does not duplicate or modify the original archive.

Turn off **Settings → Save Options → Add Random Suffixes to Output Names**
to keep the backup folder's timestamp without a random suffix. If that name
already exists, APS adds `(2)`, `(3)`, and so on, preserving previous backups.

Protected PianoSoft packages and supporting assets are retained as originals;
the conversion option does not unlock protected packages. Source files are
always unchanged.
The archive is a music backup; it does not restore a piano or create a bootable
drive clone.

## Check the result or verify it later

APS checks copied originals with SHA-256 checksums, fingerprints of their file
contents. When preparing an existing backup, it verifies the source files
against the archive's recorded checksums. Generated playback files and Yamaha
catalogs receive their own checksums.
`manifest.json` records original and destination paths, recovered metadata,
checksums, pairing evidence, timing validation, generated files, and any errors.
`README.txt` summarizes the result.

To check an existing backup:

1. Open **Utilities → Back Up Mark IV Music...** and choose **Verify backup...**.
2. Select the `MarkIV-Backup-...` or `MarkIV-Prepared-...` folder containing
   `manifest.json`.
3. Review the result and **View report...** for missing, changed, or incomplete
   files.

Verification checks retained originals, converted MIDI files, both files of
each generated MIDI/MP3 pair, encoded WAVs, and generated `PDISK.MNG` and
`PSONG.MNG` files against the manifest. It checks recorded completion status, file sizes, and
checksums. When an E-SEQ backup copy was intentionally removed, verification
checks its recorded MIDI replacement. A prepared export can be verified without
the original archive: verification checks its output files, while the manifest
retains the checksums and locations of the source files verified during preparation.
Checksums confirm file integrity; audition converted songs to assess their
playback on your player.

## If an operation stops or a drive does not respond

Drive discovery has a ten-second limit. Cancel or close the utility if a drive
does not respond; **Refresh drives** starts a fresh discovery attempt.
Cancelling an active backup waits for orderly file cleanup and retains its
partial result.

If copying, playback preparation, conversion, or catalog generation fails, APS
reports an incomplete result and keeps files that finished successfully. **Cancel** also
keeps completed files. Review **View report...** and the manifest for failures or
unfinished work; an `.incomplete` marker identifies an unfinished result.
Verification continues to report that incomplete status. Starting another
operation creates a new folder.

Keep `manifest.json` and `manifest.journal.jsonl` together if a journal remains
after interruption. **Verify backup...** uses both to check the saved progress;
it still reports an unfinished result as incomplete.

## How files and metadata are handled

### Album names and original metadata

Album and song names come from available Mark IV catalog information, folder
indexes, and song metadata. When names cannot be recovered, APS uses the
original folder or filename. Warnings identify missing or unreadable metadata.
Folder catalogs, including `PIANODIR.FIL`, `PDISK.MNG`, and `PSONG.MNG`, remain
unchanged in the archived album folders. Database evidence and catalog schemas
are retained under `_metadata/original/`.

Valid `PIANODIR.FIL` files supply E-SEQ album titles, song titles, and playback
order. Available Mark IV database labels take precedence over folder metadata.

APS reads the saved catalog directly. Catalog decoding supports the inspected
Mark IV PostgreSQL 7.3 format. Other catalog layouts use available folder indexes
and filenames.

### Optional MIDI conversion

When conversion is selected, APS converts supported E-SEQ files from verified
originals. A drive backup uses its newly copied files; preparation from an
existing backup reads its verified files in place. Each successful conversion
creates a verified `.mid` file in the new output folder. If **Keep the verified
originals** is unchecked, APS removes the backed-up E-SEQ copy only
after its MIDI file has been saved and verified. Failed or cancelled conversions
keep their originals. This retention choice applies only to the new drive
backup; an existing archive is always kept unchanged.

When **Preparing for** selects a compatible Yamaha MIDI target—DSR1, Mark II XG,
Mark III, Mark IV / PRO, or E3 / DKC-800 / DKC-850—the converted
files go into **Yamaha MIDI**, with collection and album subfolders. A numbered
folder variant is used if necessary. Each playback folder receives `PDISK.MNG`
for its album title and `PSONG.MNG` for song titles and order. Titles recovered
from `PIANODIR.FIL` are carried into these MIDI catalogs. Available compatible
MNG templates retain their other record fields; the archived catalogs remain
unchanged. When preparing an existing backup, APS matches these records using
the original drive filenames, even if the backup renamed the archived files.

For ENSPIRE / DKC-900, this cataloged playback tree is used only for albums
with existing MIDI catalogs or `PIANODIR.FIL`. Existing catalogs are updated,
and PIANODIR titles are converted; uncataloged albums do not acquire catalogs.
Their converted MIDI files use the same album organization as the archived songs.

Cataloged MIDI files use unique DOS 8.3 names, such as `SONG.mid` and
`SONG~2.mid`, and the song catalog names the actual output files. This playback
tree contains the converted songs; existing MIDI originals remain in the
archive. For other preparation profiles, converted MIDI files use the archive’s
album organization, and filename collisions receive numbered suffixes. When
preparing an existing backup, these copies are written under the new
`MarkIV-Prepared-...` folder.

Eligible E-SEQ tracks show a conversion note in the album details when
conversion is enabled. The note follows the retention choice; ordinary MIDI,
audio, and protected packages do not receive an E-SEQ conversion note.

### Free-space checks

Before copying, APS checks free space for originals, the manifest, and optional
MIDI copies. Its conservative conversion allowance is eight times the eligible
E-SEQ byte count plus at least 64 KiB per song for metadata and allocation.
This allowance also applies when originals will be removed, since both versions
coexist until each MIDI copy is verified. APS rechecks space before converting
each song and before writing its actual MIDI output. If another program fills
the destination meanwhile, failed conversions retain their verified originals.

Playback preparation also reserves space for output and temporary PCM audio.
Preparing an existing backup does not reserve space for a second archive copy.
MP3 files can expand considerably while decoding. Audio staging and
publication recovery copies stay on the destination drive. PCM staging files
are removed when each song finishes or stops; recovery copies are retained
when rollback needs them.

### How piano and accompaniment are matched

A Mark IV library can store an ordinary stereo CD recording separately from its
piano performance. The WAV does not need an encoded control channel for those
songs to be prepared. APS uses the original catalog identities retained in the
backup to find these associations, even when the files are in different album
folders.

Existing same-name MIDI/audio companions and explicit catalog companions retain
their shared timeline. Smart PianoSoft associations use recorded MIDI/audio
paths or an exact catalog CD identity and the CD track reference inside the
MIDI. Album names, song titles, and displayed track order are not used to guess
a match. Both embedded audio fingerprints must pass before APS applies the
alignment to the performance.

A saved synchronization link can identify a different CD pressing from the
piano album's original CD identity. APS follows that link's recorded CD identity
and audio filename, then validates both fingerprints against the selected audio.
When that edition is unavailable or the saved associations disagree, a complete
CD track layout can identify alternate pressings to check. Every track boundary
must agree within one second, and exactly one candidate recording must pass both
fingerprints. The manifest records the candidates and validation results.

Saved synchronization records, including `sps_offset`, are retained in the
report as evidence. APS verifies the timing again against the audio instead of
assuming those stored values have a universal meaning. Missing performances,
missing accompaniment, conflicting associations, and failed fingerprint checks
are reported. APS does not create a substitute performance for those songs.

### Encoded audio and skipped files

APS also examines WAV/MP3 files for Yamaha's phase-encoded piano control signal.
It checks both channels, validates the recovered MIDI stream, and separates the
music from the control channel. Swapped channels are detected automatically.
Short idle fragments at track boundaries are accepted only when their samples
match the idle waveform. Isolated PCM glitches are accepted only when independent
measurements confirm the same control symbols; APS does not guess replacement
MIDI bytes. These checks are recorded with the decoding results.
Recovered music is mono because one channel of the original was used for
control data. Ordinary paired recordings retain stereo accompaniment when
exported as MIDI + MP3; new encoded WAVs mix the music to mono to leave a channel
for piano control.

An audio file with neither a valid control stream nor an available associated
MIDI performance is skipped. Its original remains in the archive. A skipped
ordinary WAV is therefore not evidence that its corresponding piano recording
was lost; check the pairing report for a separately stored or missing MIDI.

An unreadable, ambiguous, or damaged control stream is reported. Lossy MP3
encoding can destroy control symbols even when the music sounds normal, so not
every encoded MP3 can be recovered. Encoded audio must be 44.1 kHz, 16-bit stereo
PCM WAV, or MP3 that decodes to that format. Ordinary paired accompaniment can
be 44.1 kHz, 16-bit mono or stereo PCM WAV, or suitable MP3. LAME is required to
read MP3 or create MIDI/MP3 output. APS checks for it before starting an operation
that needs it; install or restore LAME if that check fails.

Paired export preserves every complete audio frame declared by the WAV header.
An incomplete final frame does not cause a false truncation error; bytes outside
the declared audio are not added to the music. If complete frames are missing,
preparation fails and reports the truncated file. The original WAV is unchanged.

When a standalone encoded WAV supplies the piano performance and encoded WAV
output is selected, APS validates and copies its original bytes. MIDI/MP3 output
uses the recovered performance and clean music on their existing shared timeline.
No Smart PianoSoft fingerprint matching is needed for that standalone control
stream. When a separate MIDI performance is associated with the audio, that
MIDI supplies the performance; any embedded control audio is removed before the
music is used.

### Where playback files go

Prepared songs keep their collection and album organization under **Paired MIDI
and MP3** or **Encoded WAV**. A numbered folder variant is used when necessary.
Each MIDI/MP3 pair has matching filenames, such as `01 - Song.mid` and
`01 - Song.mp3`. Duplicate names receive the same suffix on both files.

Standalone MIDI performances are copied byte for byte into a separate **MIDI**
tree with the same album organization, for either playback format. These copies
are checksum-verified and included in the prepared-song count. Catalog wrappers
and MIDI files containing no played notes are omitted. A MIDI whose required
accompaniment is missing remains in the pairing report rather than being
presented as a complete standalone performance.

Compatible Yamaha MIDI profiles use DOS 8.3 names and album/song catalogs beside
paired output and standalone MIDI. ENSPIRE carries existing MNG or PIANODIR-derived catalogs;
uncataloged albums retain descriptive names. Keep each pair and any catalogs
together when copying its album folder to an ENSPIRE USB stick. Keep the source
backup separately.

The manifest records pairing evidence, alignment checks, decoder measurements
where applicable, and checksums for the generated files. The result counts
prepared songs and skipped audio files. A preparation failure marks the result
incomplete while retaining completed output. Cancellation removes incomplete
public pairs and retains any recovery copies identified in the report.

Paired export omits live MIDI clock, transport, and active-sensing messages,
and records their count. Musical events retain their timing or receive the
validated Smart PianoSoft alignment. Unsupported system messages produce an
error before a pair is saved. File validation does not establish physical piano
compatibility or acoustic timing; check playback on the intended instrument.

### Saved progress after interruption

During a backup or playback preparation, durable per-file updates are stored in
`manifest.journal.jsonl`, with periodic compact snapshots. This avoids rewriting
the entire library manifest for every song. An orderly finish consolidates the
manifest and removes the journal. After interruption, **Verify backup...**
replays saved progress. A partial trailing journal record from an interrupted
write is ignored.

## Preset these choices for a deployment

Source, destination, last backup folder, and the conversion and retention choices can be
preset for deployments using the [startup configuration](startup-configuration.md).
The playback-format default follows the current **Preparing for** profile each
time the dialog opens; a manual format choice applies to that dialog session.
