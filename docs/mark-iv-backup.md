# Back up a Mark IV music library

[All user guides](README.md) · [Getting started](getting-started.md)

Use **Utilities → Back Up Mark IV Music...** to copy a Disklavier Mark IV
music library into album folders with recovered names. APS reads the source,
copies the original files, and checks their contents. An optional conversion
step converts supported legacy E-SEQ songs to MIDI, with an option to keep the
originals alongside the MIDI files.

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
3. Choose **Back up music**. Progress shows the copying, verification, and
   optional conversion stages. When it finishes, check the result and any
   **View report...** messages. Use **Open backup folder** to view the files.

Each run creates a new, uniquely named `MarkIV-Backup-...` folder in the
destination. Existing backups are kept. By default, album folders contain
original-format music with unchanged file contents, including audio and
supporting assets. Portable filenames and suffixes handle names that would
otherwise collide.

Protected PianoSoft packages and supporting assets are retained as originals;
the conversion option does not unlock protected packages. Source files are
always unchanged.

## Check the result or verify it later

APS checks each copied original with a SHA-256 checksum, a fingerprint of its
file contents, and reads back generated MIDI files for the same check.
`manifest.json` records original and destination paths, recovered metadata,
checksums, generated MIDI copies, and any errors. `README.txt` summarizes the
result.

To check an existing backup:

1. Open **Utilities → Back Up Mark IV Music...** and choose **Verify backup...**.
2. Select the `MarkIV-Backup-...` folder containing `manifest.json`.
3. Review the result and **View report...** for missing, changed, or incomplete
   files.

Verification checks retained originals and generated MIDI files against the
manifest. The source drive is not needed. Checksums confirm file integrity;
audition converted songs to assess their playback on your player.

## If a backup stops or a drive does not respond

Drive discovery has a ten-second limit. Cancel or close the utility if a drive
does not respond; **Refresh drives** starts a fresh discovery attempt.
Cancelling an active backup waits for orderly file cleanup and retains its
partial result.

If copying or conversion fails, APS reports an incomplete backup and keeps
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
The backup retains available original catalogs and database evidence under
`_metadata/original/`.

APS reads the saved catalog directly. Catalog decoding supports the inspected
Mark IV PostgreSQL 7.3 format. Other catalog layouts use available folder indexes
and filenames.

### Optional MIDI conversion

When conversion is selected, APS first copies and verifies the originals,
then converts supported E-SEQ files from those copies. Each successful
conversion creates a verified `.mid` file in the same folder. If **Keep the
verified originals** is unchecked, APS removes the backed-up E-SEQ copy only
after its MIDI file has been saved and verified. Failed or cancelled conversions
keep their originals. If the intended MIDI name is already in use, the new copy
receives a numbered suffix.

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
