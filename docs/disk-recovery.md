# Recover songs from floppies and disk images

[All user guides](README.md) · [Getting started](getting-started.md)

## Open a disk and export its songs

1. For an irreplaceable physical floppy, first choose **Disk → Create Image from
   Floppy...** to keep an image copy. If the operating system offers to format
   the disk, cancel that prompt.
2. Choose **Open Image** for an image file, or **Disk → Read Floppy...** to load
   songs from a physical disk. If a drive is missing, connect it, insert a disk,
   and choose **Refresh**. If a selected drive disappears, select a drive again
   before continuing.
3. Review the songs. Choose **Save As** to export them to a new folder, or
   **File → Save As ZIP...** to create an archive.
4. Open the exported songs and check playback before relying on the recovered
   copies.

APS opens common IMG/BIN raw images and HFE images. The song list shows titles,
playback order, album information, and remaining disk space.

**Image changes outside APS:** the Linux 0.8.7 AppImage predates the source-change
safeguard. Saving an image that another tool changed while it was open can
silently replace those newer contents, including added files or repairs. Until
you have a rebuilt package verified against this case, reopen the image after
outside changes. To keep pending edits, use **Save As Image** with a new filename
first; do not select the original image as the destination. That copy contains
the session's edits and does not merge newer external changes.

Ordinary **Save** preserves unrelated files in an E-SEQ image, including notes,
MIDI files, and management catalogs present when opened, unless you explicitly
delete them.
**Save As** and **Save As Image** still prepare a clean E-SEQ delivery set.

[![A Yamaha E-SEQ floppy image with editable song titles](images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)](images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)

Recovery tools can retry difficult disks. Greaseweazle hardware also supports
SCP archives, which capture the disk's magnetic signals for preservation.
Recovery cannot guarantee that damaged recordings will play correctly.

## When exact floppy imaging fails

An unreadable or protected sector can prevent **Create Image from Floppy...**
from copying every sector even when **Read Floppy...** can read the songs.
The failure dialog offers **Create Logical Recovery**. Cancel leaves the failed
capture unpublished; choosing recovery opens **Save Logical Recovery Image**
with a separate `-logical-recovery.img` filename.

The logical image uses readable filesystem information and song clusters,
reconstructs supported Yamaha metadata where possible, and records omitted,
unreadable, or zero-filled ranges. Keep its adjacent `.recovery-….json` report.
If directory sectors cannot be read, recovery scans the disk for songs before
saving the image. Recovered songs may receive generated filenames when their
original directory entries are lost.
The result is a reconstructed working image. Exact sector preservation and
archival capture are not claimed. Check the report and recovered songs before
relying on the copy; the physical source is only read.

## Repair a Yamaha image or make its files visible

A boot sector is the part of a disk image that describes its layout. Use
**Disk → Repair Yamaha Boot Sector...** when an otherwise supported Yamaha
image needs this repair or its files are hidden in an emulator disk browser.
This utility changes the selected image **in place**; its backups are off by
default.

1. Choose **Image file**, then **Browse...** for an IMG, IMA, BIN, VFD, or HFE
   file. Choose **Entire folder** instead to process a directory, optionally
   including subfolders.
2. Check **Create a backup before repairing** to keep the original as `.bak`
   (or a numbered backup if one already exists).
3. Leave **Keep current format** selected, or choose a target such as
   **HFE (Nalbantov) image** or **IMG (Gotek) raw sector image**. Changing format
   keeps both the repaired original and a converted file beside it.
4. Choose **Repair** and review the result for each image. Existing converted
   files are reported as conflicts and left untouched.

### What the repair changes

The utility recognizes protected 720 KB, 800 KB, and 1.44 MB FAT12 layouts.
It repairs the boot sector and clears hidden/system attributes on files and
folders so Nalbantov/HxC Disk Browser can display them. File contents,
read-only/archive attributes, FAT copies, and unused space are preserved.
Directories are not rebuilt. If the first sector was omitted from a raw image,
it adds one. HFE conversion requires Greaseweazle and verifies decoded sector
data before saving. Images with valid boot sectors still have hidden/system
flags removed; images that need neither change are left alone unless conversion
is requested. Unrecognized layouts are rejected. Pending song edits are not applied.
If the Yamaha catalog identifies an image whose file directory is also damaged,
the utility directs you to **Recover Damaged Image** and leaves the image untouched.

## Recover songs from a damaged image

Choose **Disk → Recover Damaged Image...** to recover song data into a new
editable image copy. Review the recovered songs, then use **Save As** for a
folder or **Save As Image** for an image copy. The source image is unchanged.
Recovery cannot guarantee that damaged recordings will play correctly.

Normal FAT12 image loading checks its redundant allocation tables. A sole
usable copy can supply the working image when another copy is damaged. If
viable copies disagree on allocation, APS stops with recovery guidance instead
of selecting one arbitrarily. File and directory clusters must fit within the
image's data area during the initial scan.

Recovery tries each usable allocation table before carving contiguous song
data. If competing chains produce different plausible versions of the same
song, it reports the ambiguity. A broken chain is never treated as evidence
that a file was contiguous. E-SEQ carving checks stream boundaries as well as
header lengths, which some Yamaha files undercount or saturate. Source images
remain unchanged.

**Disk → Recover Damaged Image...** also clears hidden/system attributes in the
recovered image. Both repair tools make files visible automatically, including
when processing images that were repaired previously. This visibility change
leaves the contents of the files unchanged.

## Keep a partial floppy capture

If USB-floppy recovery fails or is cancelled, **Disk → Save Partial Capture...**
keeps the recovered image together with sector coverage and diagnostics, without
reading the disk again. Unread portions are identified in the diagnostics.

For stalled image creation or a Windows `_MEI…` temporary-directory warning,
see [Windows disk troubleshooting](windows-disk-troubleshooting.md).

## Extract a collection of images

For collections of images, **Utilities → Bulk Extraction...** can keep a local
progress record. **Resume extraction job...** retries failed items first and
reuses completed outputs only after checking input and output hashes.
Progress JSON files and their lock files are removed after successful extraction.
Failed or cancelled jobs keep their progress records so you can resume them.

## Back up a Mark IV library

**Utilities → Back Up Mark IV Music...** copies a mounted Mark IV music library
or copied data folder into named album folders, verifies the originals, and can
add MIDI copies of legacy E-SEQ songs. See the [Mark IV backup guide](mark-iv-backup.md).

## Optional review invitation

After three successful physical floppy reads, APS offers an optional review
invitation. **Remind me later** waits for three more successful reads;
**Never ask again** permanently dismisses it. **Write a review** opens the
review page in your browser and dismisses future invitations. These choices
are remembered across launches.

[All user guides](README.md) · [Review, save, verify, and resume](preparation-reliability.md)
