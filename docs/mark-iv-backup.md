# Back up a Mark IV music library

Use **Utilities → Back Up Mark IV Music...** to copy a Disklavier Mark IV
music library into album folders with recovered names. APS reads the source,
copies the original files, and checks their contents. An optional conversion
step converts supported legacy E-SEQ songs to MIDI, with an option to keep the
originals alongside the MIDI files.

## Choose and scan the source

1. Make the Mark IV data partition available as a readable, mounted folder,
   or use an existing copy of its data folder. Select the folder containing
   **`songs/`**, rather than `songs/` itself.
2. Open **Utilities → Back Up Mark IV Music...**. Choose the source from the
   drive list or use **Browse...** to select its folder. **Refresh drives**
   updates the list of mounted drives.
3. Choose **Scan music**. Review the album names, collection, file counts, and
   sizes. Select an album to see its track list, file types, sizes, and available
   album information in **Details**. Double-click an album to open a larger view
   with track metadata, source and backup paths, and supporting files.
   Drag the divider above **Details** to resize it relative to the album list.
   Scan warnings and operation errors appear separately under **View report...**.

Mounted-drive discovery is available on Linux and Windows. APS does not mount
partitions or install filesystem drivers; the operating system must already
be able to read the source. A copied data folder works on either platform.
APS opens source files only for reading. A read-only mount can also enforce
that at the operating-system level.

Use an offline data volume or a stable copy while scanning and backing up.
APS reads the saved catalog directly; it does not connect to a running piano
database. Catalog decoding supports the inspected Mark IV PostgreSQL 7.3
format. Other catalog layouts use available folder indexes and filenames.

Album and song names come from available Mark IV catalog information, folder
indexes, and song metadata. When names cannot be recovered, APS uses the
original folder or filename. Warnings identify missing or unreadable metadata.
The backup retains available original catalogs and database evidence under
`_metadata/original/`.

## Make the backup

1. Select a **Backup destination** outside the source. For a mounted music
   drive, use a separate destination filesystem.
2. Optionally select **Also convert legacy E-SEQ files to MIDI**. This is off
   by default, and APS remembers the choice.
   **Keep the verified originals and save MIDI copies alongside them** becomes
   available when conversion is on and is checked by default. Uncheck it to
   keep only the MIDI version of successfully converted E-SEQ songs in the
   backup. APS remembers this choice too.
   Eligible E-SEQ tracks show a conversion note in the album details when
   conversion is enabled. The note follows the retention choice; ordinary MIDI,
   audio, and protected packages do not receive an E-SEQ conversion note.
3. Choose **Back up music**. Progress shows the copying, verification, and
   optional conversion stages. Use **Open backup folder** to view the result.

Each run creates a new, uniquely named `MarkIV-Backup-...` folder in the
destination. Existing backups are kept. Album folders contain original-format
music with unchanged file contents, including audio and supporting assets.
Portable filenames and suffixes handle names that would otherwise collide.

Before copying, APS checks free space for originals, the manifest, and optional
MIDI copies. Its conservative conversion allowance is eight times the eligible
E-SEQ byte count plus at least 64 KiB per song for metadata and allocation.
This allowance also applies when originals will be removed, since both versions
coexist until each MIDI copy is verified. APS rechecks space before converting
each song and before writing its actual MIDI output. If another program fills
the destination meanwhile, failed conversions retain their verified originals.

When conversion is selected, APS first copies and verifies the originals,
then converts supported E-SEQ files from those copies. Each successful
conversion creates a verified `.mid` file in the same folder. If **Keep the
verified originals** is unchecked, APS removes the backed-up E-SEQ copy only
after its MIDI file has been saved and verified. Failed or cancelled conversions
keep their originals. Source files are always unchanged. If the intended MIDI
name is already in use, the new copy receives a numbered suffix.

Protected PianoSoft packages and supporting assets are retained as originals;
the conversion option does not unlock protected packages. The backup is a
music archive. Restoring a piano and creating a bootable drive clone are
outside this utility's scope.

## Check the result or verify it later

APS checks each copied original with a SHA-256 checksum, a fingerprint of its
file contents, and reads back generated MIDI files for the same check.
`manifest.json` records original and destination paths, recovered metadata,
checksums, generated MIDI copies, and any errors. `README.txt` summarizes the
result.

To check an existing backup, open the utility, choose **Verify backup...**, and
select the `MarkIV-Backup-...` folder containing `manifest.json`. Verification
checks retained originals and generated MIDI files against the manifest and
reports missing, changed, or incomplete files under **View report...**. The source drive
is not needed for this check. Checksums confirm file integrity; audition
converted songs to assess their playback on your player.

If copying or conversion fails, APS reports an incomplete backup and keeps
the retained originals and MIDI files that finished successfully. **Cancel** also
keeps completed files. Review **View report...** and the manifest for failures or
unfinished work; an `.incomplete` marker identifies an unfinished backup.
Verification continues to report that incomplete status. Starting another
backup creates a new folder.

Source, destination, last backup folder, and the conversion and retention choices can be
preset for deployments using the [startup configuration](startup-configuration.md).
