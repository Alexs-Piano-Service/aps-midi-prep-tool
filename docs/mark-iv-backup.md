# Back up a Mark IV music library

[All user guides](README.md) · [Getting started](getting-started.md)

Use **Utilities → Back Up Mark IV Music...** to copy a Disklavier Mark IV
music library into album folders with recovered names. APS reads the source,
copies the original files, and checks their contents. An optional conversion
step converts supported legacy E-SEQ songs to MIDI, with an option to keep the
verified originals in the archive.
For ENSPIRE delivery, the utility can also decode Yamaha control audio in
WAV/MP3 files into synchronized MIDI and clean music copies.

The result is a music archive. Restoring a piano and creating a bootable drive
clone are outside this utility's scope.

## Before you start

- Make the Mark IV data partition available as a readable, mounted folder,
  or use an existing copy of its data folder. You need the folder containing
  **`songs/`**, rather than `songs/` itself.
- Use an offline data volume or a stable copy while scanning and backing up.
  APS does not connect to a running piano database.
- Choose a destination outside the source. For a mounted music drive, use a
  separate destination filesystem with room for the backup and any MIDI copies.

Mounted-drive discovery is available on Linux and Windows. APS does not mount
partitions or install filesystem drivers; the operating system must already
be able to read the source. A copied data folder works on either platform.
APS opens source files only for reading. A read-only mount can also enforce
that at the operating-system level.

## Choose and scan the source

1. Open **Utilities → Back Up Mark IV Music...**.
2. Choose the source folder containing **`songs/`** from the
   drive list or use **Browse...** to select its folder. **Refresh drives**
   updates the list of mounted drives.
3. Choose **Scan music**. Review the album names, collection, file counts, and
   sizes. Select an album to see its track list, file types, sizes, and available
   album information in **Details**. Check **View report...** for scan warnings
   or errors before continuing.

Double-click an album to open a larger view with track metadata, source and
backup paths, and supporting files. Drag the divider above **Details** to
resize it relative to the album list. Selecting an album shows its details;
the backup includes the scanned library.

## Make the backup

1. Select a **Backup destination** outside the source. For a mounted music
   drive, use a separate destination filesystem.
2. Optionally select **Also convert legacy E-SEQ files to MIDI**. This is off
   by default, and APS remembers the choice.
   **Keep the verified originals and save MIDI copies alongside them** becomes
   available when conversion is on and is checked by default. Uncheck it to
   keep only the MIDI version of successfully converted E-SEQ songs in the
   backup. APS remembers this choice too.
3. Review **Create paired MIDI + MP3 files from encoded WAV/MP3**. It is selected
   automatically when **Preparing for** is ENSPIRE / DKC-900 and starts off for
   other targets. You can change it for this backup. This option always keeps
   the original audio, independently of the E-SEQ retention option.
4. Choose **Back up music**. Progress shows the copying, verification, and
   optional conversion stages. When it finishes, check the result and any
   **View report...** messages. Use **Open backup folder** to view the files.

Each run creates a new, uniquely named `MarkIV-Backup-...` folder in the
destination. Existing backups are kept. By default, album folders contain
original-format music with unchanged file contents, including audio and
supporting assets. Portable filenames and suffixes handle names that would
otherwise collide.

Turn off **Settings → Save Options → Add Random Suffixes to Output Names**
to keep the backup folder's timestamp without a random suffix. If that name
already exists, APS adds `(2)`, `(3)`, and so on, preserving previous backups.

Protected PianoSoft packages and supporting assets are retained as originals;
the conversion option does not unlock protected packages. Source files are
always unchanged.

## Check the result or verify it later

APS checks each copied original with a SHA-256 checksum, a fingerprint of its
file contents, and reads back generated MIDI files and Yamaha catalogs for the
same check.
`manifest.json` records original and destination paths, recovered metadata,
checksums, generated MIDI copies, decoded audio pairs, generated catalogs, and
any errors. `README.txt` summarizes the result.

To check an existing backup:

1. Open **Utilities → Back Up Mark IV Music...** and choose **Verify backup...**.
2. Select the `MarkIV-Backup-...` folder containing `manifest.json`.
3. Review the result and **View report...** for missing, changed, or incomplete
   files.

Verification checks retained originals, converted MIDI files, both files of
each generated audio pair, and generated `PDISK.MNG` and `PSONG.MNG` files
against the manifest. It checks recorded completion status, file sizes, and
checksums. When an E-SEQ backup copy was intentionally removed, verification
checks its recorded MIDI replacement. The source drive is not needed.
Checksums confirm file integrity; audition converted songs to assess their
playback on your player.

## If a backup stops or a drive does not respond

Drive discovery has a ten-second limit. Cancel or close the utility if a drive
does not respond; **Refresh drives** starts a fresh discovery attempt.
Cancelling an active backup waits for orderly file cleanup and retains its
partial result.

If copying, conversion, or catalog generation fails, APS reports an incomplete backup and keeps
the retained originals and MIDI files that finished successfully. **Cancel** also
keeps completed files. Review **View report...** and the manifest for failures or
unfinished work; an `.incomplete` marker identifies an unfinished backup.
Verification continues to report that incomplete status. Starting another
backup creates a new folder.

Keep `manifest.json` and `manifest.journal.jsonl` together if a journal remains
after interruption. **Verify backup...** uses both to check the saved progress;
it still reports an unfinished backup as incomplete.

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

When conversion is selected, APS first copies and verifies the originals,
then converts supported E-SEQ files from those copies. Each successful
conversion creates a verified `.mid` file. If **Keep the verified originals**
is unchecked, APS removes the backed-up E-SEQ copy only
after its MIDI file has been saved and verified. Failed or cancelled conversions
keep their originals.

When **Preparing for** selects a compatible Yamaha MIDI target—DSR1, Mark II XG,
Mark III, Mark IV / PRO, or E3 / DKC-800 / DKC-850—the converted
files go into **Yamaha MIDI**, with collection and album subfolders. A numbered
folder variant is used if necessary. Each playback folder receives `PDISK.MNG`
for its album title and `PSONG.MNG` for song titles and order. Titles recovered
from `PIANODIR.FIL` are carried into these MIDI catalogs. Available compatible
MNG templates retain their other record fields; the archived catalogs remain
unchanged.

For ENSPIRE / DKC-900, this cataloged playback tree is used only for albums
with existing MIDI catalogs or `PIANODIR.FIL`. Existing catalogs are updated,
and PIANODIR titles are converted; uncataloged albums do not acquire catalogs.
Their converted MIDI files stay beside the archived songs.

Cataloged MIDI files use unique DOS 8.3 names, such as `SONG.mid` and
`SONG~2.mid`, and the song catalog names the actual output files. This playback
tree contains the converted songs; existing MIDI originals remain in the
archive. For other preparation profiles, converted MIDI files stay beside the
archived songs and filename collisions receive numbered suffixes.

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

Audio decoding also reserves space for the MIDI/MP3 copies and temporary PCM
audio. MP3 files can expand considerably while decoding. Audio staging and
publication recovery copies stay on the destination drive. PCM staging files
are removed when each song finishes or stops; recovery copies are retained
when rollback needs them.

### Optional encoded-audio decoding

With **Create paired MIDI + MP3 files from encoded WAV/MP3** selected, APS
processes verified backup copies after copying the entire source library. It
checks both channels for Yamaha's phase-encoded control signal, validates the
MIDI stream, and writes the other channel as clean music. Swapped channels are
detected automatically. The recovered music is mono because one channel of
the encoded original was used for control data.

Successful pairs are saved separately under **Paired MIDI and MP3**, preserving
the collection and album organization. If that folder name is occupied, APS
uses a numbered variant. The two files for a song have the same basename. With
a compatible Yamaha MIDI profile selected, pairs use DOS 8.3 names, such as
`SONG.mid` and `SONG.mp3`, and the playback folder includes album and song titles
in `PDISK.MNG` and `PSONG.MNG`. Other profiles retain descriptive names such as
`01 - Song.mid` and `01 - Song.mp3`. Duplicate names receive the same suffix on
both files. ENSPIRE only carries catalogs for albums with existing MNG or
PIANODIR metadata; uncataloged albums retain descriptive names. Copy the desired
album folder to an ENSPIRE USB stick, keeping each
pair and its catalogs together. Keep the full backup separately.

The MIDI and music retain the encoded recording's shared timeline. APS does
not run Smart PianoSoft fingerprint matching again on an already synchronized
control track. It does not add a guessed mechanical-delay correction. MIDI
completion times are recovered from the signal; exact acoustic alignment and
the piano's MP3 delay handling still require a playback check.

Paired export omits MIDI clock, transport, and active-sensing messages from the
recovered stream and records their count. Notes, pedals, and other musical
events retain their recorded times. System-common and system-reset messages
produce an error identifying the message and its time before a pair is saved.

APS inspects eligible audio even when a MIDI or E-SEQ file has the same basename;
the companion alone does not establish that the audio is clean. Audio without
a control signal is kept without creating derivatives. Encoded audio receives
a new MIDI/MP3 pair without replacing the archived audio or companion files.
Metadata, protected packages, and presentation dependencies are also preserved.
An unreadable, ambiguous, or damaged control stream is reported without creating
a guessed performance. Lossy MP3 encoding can destroy control symbols even
when the music sounds normal, so not every MP3 can be recovered. WAV input must
be 44.1 kHz, 16-bit stereo PCM; MP3 input must decode to that format. LAME is
required to decode MP3 and create the clean MP3 output. When decoding is selected
and eligible audio files exist, APS checks for LAME before creating the backup
folder or copying any files. If it is missing, install LAME or turn off the
encoded-audio option to make an originals-only audio backup.

The manifest records the detected control channel, decoder measurements,
timing, and separate checksums for both output files and any generated catalogs.
The result counts decoded
pairs and skipped audio files. A decode failure marks the overall backup
incomplete but retains all verified originals and any earlier completed pairs.
Cancelling removes incomplete public pairs while preserving reported recovery
copies when needed.

### Saved progress after interruption

During a backup, durable per-file updates are stored in
`manifest.journal.jsonl`, with periodic compact snapshots. This avoids rewriting
the entire library manifest for every song. An orderly finish consolidates the
manifest and removes the journal. After interruption, **Verify backup...**
replays saved progress. A partial trailing journal record from an interrupted
write is ignored.

## Preset these choices for a deployment

Source, destination, last backup folder, and the conversion and retention choices can be
preset for deployments using the [startup configuration](startup-configuration.md).
The audio-decoding default follows the current **Preparing for** profile each
time the dialog opens; it is not a remembered global switch.
