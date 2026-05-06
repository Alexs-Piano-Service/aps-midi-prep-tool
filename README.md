# APS MIDI Prep Tool

APS MIDI Prep Tool is a modern Disklavier preservation and preparation
workstation for MIDI files, Yamaha E-SEQ files, floppy images, and physical
floppy disks.

Current version: `0.6.1`

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
- Creates fresh floppy images, including default PianoSoft-style 720K E-SEQ images.
- Formats Yamaha Disklavier floppies as MIDI or E-SEQ disks.
- Recovers damaged floppy images and physical floppies with repair and raw-carving paths.
- Edits MIDI titles, E-SEQ titles, image filenames, and Disklavier album metadata.
- Converts MIDI Type 1 / SMF1 files to MIDI Type 0 / SMF0.
- Converts Yamaha E-SEQ `.FIL` files to standard MIDI.
- Converts MIDI to Yamaha E-SEQ, converting Type 1 MIDI to Type 0 first when needed.
- Generates and refreshes `PIANODIR.FIL` for Yamaha E-SEQ disks and folders.
- Stages destructive or format-changing work until you choose `Save`, `Save As`,
  `Save As Image`, or `Write Current Image to Floppy`.
- Shows song lists, file inspection metadata, piano-roll previews, channels, and
  playback previews.
- Optionally writes `.tags.txt` ID3 sidecar files for local folder exports.

## Common Workflows

### Preserve A Yamaha Floppy

1. Insert the disk. If your operating system asks to format it, cancel.
2. Choose `Read Floppy`.
3. Select `Floppy Drive` or `Greaseweazle`.
4. Review the files, titles, types, and free space.
5. Use `Save As Image` to create an archival image, or `Save As` to extract files.

For difficult disks, choose `Read Floppy` and enable `Start in recovery mode`.
Recovery asks for the disk size, defaults to the common Yamaha 720K DD format,
copies a full image first, then tries filesystem repair and raw MIDI/E-SEQ carving.

### Prepare A Nalbantov USB Stick Image

1. Open an existing HFE image with `Open Image`, or use `File -> New Image...`.
2. Use the default 720K E-SEQ image settings for PianoSoft-style disks.
3. Drag MIDI or E-SEQ files into the table.
4. In E-SEQ image mode, dropped MIDI files are staged as E-SEQ conversions.
5. Edit titles and order as needed.
6. Use `Save As Image` and choose HFE output.

### Convert MIDI Type 1 To Type 0

1. Open a MIDI folder or drag MIDI files into the window.
2. Check the `Type` column.
3. Choose `SMF1 -> SMF0`.
4. Review the staged changes.
5. Use `Save` to overwrite originals, or `Save As` to write copies.

The conversion is staged first. Original files are not modified until you save.

### Convert Between MIDI And E-SEQ

- In MIDI mode, dropped E-SEQ files are staged as MIDI conversions.
- In E-SEQ mode, dropped MIDI files are staged as E-SEQ conversions.
- In E-SEQ image or floppy modes, dropped MIDI files are staged as E-SEQ and
  converted through Type 0 first when necessary.
- `PIANODIR.FIL` is generated or refreshed on save when needed.

### Inspect A Song

Use `Utilities -> File Inspection...`, or double-click a song's `Type` field.
The inspection window shows a piano roll, metadata, tracks, channels, controller
notes, selectable channels, position control, and playback preview.

### Make A Copyable Song List

Use `Utilities -> Song List...` to create a clean copyable list of the current
album and songs. Extra spaces in Disklavier-centered titles are collapsed for
readable reference lists.

### Create Tag Sidecar Files

Enable `File -> Create Tag Sidecars When Saving` before saving local folder
files. When enabled, APS MIDI Prep Tool writes one UTF-8 `.tags.txt` file next
to each saved MIDI or E-SEQ file. These sidecars use four-letter ID3 tag keys
such as `TIT2` for title and `TALB` for album.

Tag sidecar files are not written when saving in Image Mode or Floppy Mode.

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
