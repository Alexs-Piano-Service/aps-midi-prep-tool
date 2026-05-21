# APS MIDI Prep Tool

APS MIDI Prep Tool is a modern Disklavier preservation and preparation
workstation for MIDI files, Yamaha E-SEQ files, floppy images, and physical
floppy disks.

Current version: `0.6.4`

Author: Alexander Peppe

Company: Alex's Piano Service LLC

Address: 432 Elm St., Biddeford, ME 04005

License: Apache License 2.0

The tool is built around practical Disklavier workflows: preserving old Yamaha
floppies, preparing Nalbantov emulator images, correcting song titles, converting
between MIDI and E-SEQ, making SMF0-compatible files, and inspecting songs
before you write anything back.

## What It Does

- Opens MIDI folders, Yamaha E-SEQ folders, floppy images, and physical floppies.
- Reads common floppy-image formats supported through Greaseweazle conversion,
  including IMG/BIN-style raw images and HFE workflows.
- Reads and writes physical floppies using a normal floppy drive or Greaseweazle.
- Shows Greaseweazle sector-map previews after reads, writes, and image conversions
  so good and bad sector positions can be reviewed visually.
- Images physical floppies directly to IMG or SCP files without opening or
  scanning the disk contents afterward.
- Recognizes Macintosh 800K GCR/HFS Greaseweazle SCP captures as non-Yamaha
  disks and can save decoded IMG copies without opening them for editing.
- Creates fresh floppy images, including default PianoSoft-style 720K E-SEQ images.
- Formats Yamaha Disklavier floppies as MIDI or E-SEQ disks.
- Formats removable USB sticks as FAT32 superfloppies for E3/ENSPIRE Disklaviers
  or as MBR single-partition FAT32 disks for PianoForce.
- Recovers damaged floppy images and physical floppies with repair and raw-carving paths.
- Edits MIDI titles, E-SEQ titles, image filenames, and Disklavier album metadata.
- Trims Disklavier-spaced title text into regular MIDI titles during floppy reads
  or as a batch utility.
- Converts MIDI Type 1 / SMF1 files to MIDI Type 0 / SMF0.
- Converts Yamaha E-SEQ `.FIL` files to standard MIDI.
- Extracts Akai MPC `.SEQ` files and embedded sequences in MPC `.ALL` files,
  converting them to standard MIDI.
- Extracts Yamaha V50/SY77 NSEQ sequences when the V50/SY77 signature is
  present, converting them to standard MIDI.
- Reads Yamaha Electone MDR floppy images, including some images with blank or
  nonstandard boot sectors, and converts `.EVT` performance files to standard
  MIDI while preserving millisecond timing and SysEx events.
- Converts MIDI to Yamaha E-SEQ, converting Type 1 MIDI to Type 0 first when needed.
- Generates and refreshes `PIANODIR.FIL` for Yamaha E-SEQ disks and folders.
- Stages destructive or format-changing work until you choose `File > Save`,
  `File > Save As...`, `File > Save As Image...`, or
  `Disk > Write Current Image to Floppy...`.
- Can write a `metadata_summary.txt` file on save with each saved MIDI file and
  its detected MIDI metadata.
- Keeps save behavior in `File > Save Options`, including album subfolders,
  backups, tag sidecars, and metadata summaries.
- Protects original images and floppies with
  `File > Write Protection > Write-Protect Original`.
- Shows song lists, file inspection metadata, piano-roll previews, channels, and
  playback previews.
- Provides a `View` menu for title-warning display, Disklavier title formatting,
  status visibility, quick-panel visibility, album-info visibility, and realtime
  console logs.
- Shows an empty-list drop target and highlights the file list during supported
  file drags so MIDI and E-SEQ files are easier to add.
- Provides `Help > Report a Bug...` for sending a support report with app
  details and optional recent console logs.
- Provides customizable keyboard shortcuts for current File, Disk, View,
  Utilities, Settings, and Help commands.
- Optionally writes `.tags.txt` ID3 sidecar files for local folder exports.

## Common Workflows

### Preserve A Yamaha Floppy

1. Insert the disk. If your operating system asks to format it, cancel.
2. Choose `Disk > Read Floppy...`.
3. Select `Floppy Drive` or `Greaseweazle`.
4. Optionally enable `Trim title spaces after reading` to clean centered
   Disklavier-screen titles into regular MIDI titles.
5. Review the files, titles, types, and free space.
6. Use `File > Save As Image...` to create an archival image, or
   `File > Save As...` to extract files.

For difficult disks, choose `Disk > Read Floppy...` and enable
`Start in recovery mode`. Recovery asks for the disk size, defaults to the
common Yamaha 720K DD format, copies a full image first, then tries filesystem
repair and raw MIDI/E-SEQ carving.

To make a preservation copy without opening the disk contents in the app, choose
`Disk > Image Floppy...` and save an IMG sector image or Greaseweazle SCP flux
capture.

### Save Safely

`File > Write Protection > Write-Protect Original` keeps `File > Save` from
overwriting the current image or floppy until you explicitly turn that
protection off. `File > Save As...` and `File > Save As Image...` remain
available for copy-based work.

Enable `File > Save Options > Back up before Saving` when you want APS MIDI
Prep Tool to create a backup before overwriting local files or disk images.

Enable `File > Save Options > Create Album Subfolder` when exporting Yamaha
E-SEQ files and you want the destination folder grouped by the current album
title and catalog number.

### Prepare A Nalbantov USB Stick Image

1. Open an existing HFE image with `File > Open > Open Image...`, or use
   `File > New Image...`.
2. Use the default 720K E-SEQ image settings for PianoSoft-style disks.
3. Drag MIDI or E-SEQ files into the table.
4. In E-SEQ image mode, dropped MIDI files are staged as E-SEQ conversions.
5. Edit titles and order as needed.
6. Use `File > Save As Image...` and choose HFE output.

### Format A Removable USB Stick

Use `Disk > Format USB Stick...` to prepare a removable USB stick as FAT32.
Choose the superfloppy layout for Disklavier and floppy-emulator workflows, or
the single-partition MBR layout for PianoForce and devices that expect a normal
partitioned USB stick. The dialog previews the selected device and current
contents before formatting.

### Convert MIDI Type 1 To Type 0

1. Open a MIDI folder or drag MIDI files into the window.
2. Check the `Type` column.
3. Choose `Utilities > Convert > Convert All SMF1 to SMF0`.
4. Review the staged changes.
5. Use `Save` to overwrite originals, or `Save As` to write copies.

The conversion is staged first. Original files are not modified until you save.

### Convert Between MIDI And E-SEQ

- In MIDI mode, dropped E-SEQ files are staged as MIDI conversions.
- In E-SEQ mode, dropped MIDI files are staged as E-SEQ conversions.
- In E-SEQ image or floppy modes, dropped MIDI files are staged as E-SEQ and
  converted through Type 0 first when necessary.
- `PIANODIR.FIL` is generated or refreshed on save when needed.
- `File > Save Options > Create Album Subfolder` controls whether folder
  exports use the current album title and catalog number for a subfolder.

### Inspect A Song

Use `Utilities > File Inspection...`, or double-click a song's `Type` field.
The inspection window shows a piano roll, metadata, tracks, channels, controller
notes, selectable channels, position control, and playback preview.

### Make A Copyable Song List

Use `Utilities > Song List...` to create a clean copyable list of the current
album and songs. Extra spaces in Disklavier-centered titles are collapsed for
readable reference lists.

Use `Utilities > Trim Title Spaces` to stage that same cleanup for the listed
MIDI or E-SEQ titles before saving.

### Create Tag Sidecar Files

Enable `File > Save Options > Create Tag Sidecars When Saving` before saving
local folder files. When enabled, APS MIDI Prep Tool writes one UTF-8
`.tags.txt` file next to each saved MIDI or E-SEQ file. These sidecars use
four-letter ID3 tag keys such as `TIT2` for title and `TALB` for album.

Tag sidecar files are not written when saving in Image Mode or Floppy Mode.

### View Logs And Shortcuts

`View > Hide Status` is checked by default and hides the status text beneath the
file list. `View > Hide Quick Panel` hides the Options, Utilities, and File
Actions panel when you want more table space. `View > Hide Album Info` hides
the Album Title and Catalog Number fields, which otherwise stay visible so Save
As and album-folder options can use the current disk metadata.

Use `View > View Logs...` to open a live console-output window for the current
session. It is useful when checking Greaseweazle, mtools, format, or conversion
output while the app is still running.

Use `Help > Report a Bug...` to send a bug report. The dialog can include a
large recent tail of the live console log along with app and operating-system
details.

Use `Settings > Keyboard Shortcuts...` to review or customize the default
hotkeys for all current File, Disk, View, Utilities, Settings, and Help menu
commands.

## Modes

### MIDI Mode

Use this for normal `.mid` or `.midi` folders. You can edit titles, rename files
to DOS 8.3, convert SMF1 to SMF0, and save in place or to a new folder.

### E-SEQ Mode

Use this for local Yamaha `.FIL` files and local `PIANODIR.FIL` workflows. The
tool manages the special PIANODIR row separately and can preserve or refresh
album metadata.

### Image Mode

Use this for floppy images. You can stage adds, removals, renames, title edits,
MIDI/E-SEQ conversions, and image exports without modifying the original image
until you save.

### Floppy Mode

Use this after reading or formatting a physical floppy. Save operations can write
back to the same device when explicitly allowed, or you can export to a folder or
image first.

## Requirements

For normal source runs:

- Python 3.10+
- PySide6
- `mtools` for image authoring and FAT image operations
- Greaseweazle CLI (`gw`) for Greaseweazle reads, writes, and conversions
- FluidSynth and a redistributable GM/piano SoundFont for File Inspection playback
- Device permissions for direct floppy drive reads and writes

Release AppImages bundle the needed `mtools` commands, Greaseweazle CLI,
FluidSynth, and a SoundFont when available. Physical floppy access still
depends on operating-system device permissions.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6
python3 aps_midi_prep_tool.py
```

## Update Checks

`Help -> Check for Updates...` reads this public JSON URL:

```text
https://www.alexanderpeppe.com/aps-midi-prep-tool-data/update.json
```

Startup update checks are enabled by default and can be disabled from the Help
menu or from the startup update notice.

## Project Files

- `LICENSE`: Apache License 2.0.
- `NOTICE`: copyright and attribution notice.
- `CHANGELOG.md`: release history and upcoming changes.
- `SECURITY.md`: how to report bugs, suspicious behavior, and false positives.
- `CONTRIBUTING.md`: contribution and test-copy guidance.
- `aps_midi_prep_tool_app/eseq_reference.md`: E-SEQ and PIANODIR engineering notes.

## Related Guides

- [Extracting MIDI Files from a Yamaha Floppy Disk with APS MIDI Prep Tool](https://www.alexanderpeppe.com/extracting-midi-files-from-a-yamaha-floppy-disk-with-aps-midi-prep-tool/)
- [How to Change MIDI Titles on Your Computer Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/change-midi-titles-aps-midi-prep-tool/)
- [Copying a Yamaha PianoSoft Floppy Disk to a Nalbantov USB Stick](https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/)
- [Converting MIDI Files From Type 1 to Type 0 Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/converting-midi-files-type-1-to-type-0-aps-midi-prep-tool/)
- [Adding, Removing, or Changing Titles in Nalbantov USB Stick Virtual Disks](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/)

## Disclaimer

APS MIDI Prep Tool is an independent utility for lawful preservation, repair,
and compatibility work. Use copies whenever possible, keep backups, and test
outputs before relying on them. You are responsible for any data loss, disk
damage, instrument behavior, or other results from using the software.

Use the tool only with disks and files you own or are authorized to preserve,
convert, or modify. Do not use it to distribute copyrighted music, commercial
player-piano libraries, proprietary software, or other material you do not have
the right to share.

This project is not affiliated with Yamaha, PianoDisc, Nalbantov, Greaseweazle,
or other companies mentioned. Trademarks belong to their respective owners.

Related Alex's Piano Service LLC policies:

- [Disclaimer](https://www.alexanderpeppe.com/disclaimer/)
- [Privacy Policy](https://www.alexanderpeppe.com/privacy-policy/)
- [DMCA Policy](https://www.alexanderpeppe.com/dmca-policy/)
