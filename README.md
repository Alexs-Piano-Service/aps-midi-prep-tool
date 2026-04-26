# APS MIDI Prep Tool

`APS MIDI Prep Tool` is a desktop tool for editing MIDI track titles with legacy playback systems in mind.
Current version: `0.5.2` (2026).

Built for the workflows at [AlexanderPeppe.com](https://www.alexanderpeppe.com/), this project helps players, technicians, and collectors keep MIDI files named properly and compatible with older Disklavier setups.

## Why This Exists

Many older systems are sensitive to modern filenames and metadata. This app focuses on practical compatibility tasks:

- Edit MIDI titles quickly in bulk
- Keep track names safer for older hardware
- Rename files to DOS 8.3 format when needed
- Create backups before making changes
- Leaving XG data intact

## Features

- Load all `.mid` / `.midi` files from a folder
- Drag and drop MIDI files into the table
- Click titles to edit and queue changes
- Optional warning for titles longer than 32 characters
- Save updates directly to source files
- `Save As...` to write updated copies to another folder
- Drop a floppy image (`.img`, `.hfe`, and other Greaseweazle-supported formats) to enter Image Mode
- Read a 720K or 1.44M USB floppy directly into Floppy Mode and save changes back to the disk
- On Linux, USB floppy reads use a fast FAT12 file-level path when possible, including Yamaha 720K disks with a blank/corrupt sector 0
- Read a floppy into Floppy Mode through Greaseweazle, including drive selection (`0`, `1`, `2`, `A`, `B`)
- Edit MIDI titles inside a fixed-size floppy image or floppy session, rename/remove/add files, and repair Yamaha 720K copy protection on save/export
- Detect Yamaha E-SEQ disks, show `PIANODIR.FIL` as a separate status row, and offer to generate or refresh it on save
- View and edit Yamaha E-SEQ title metadata stored in the fixed title field inside each E-SEQ file
- Convert listed files inside Image Mode and Floppy Mode between Yamaha E-SEQ (`.FIL`) and SMF MIDI (`.MID`), including E-SEQ tempo metadata
- One-click `Rename All to DOS 8.3`
- Optional backup files (`*_backup.mid`) before changes

## Legacy Disklavier Notes

For older Disklavier and floppy-disk style workflows, these practices are usually safest:

- Use short titles
- Prefer printable ASCII characters
- Use DOS 8.3 filenames when exchanging files with legacy media or software
- Keep backups before large renaming operations

This app includes tools for all of the above.

## Quick Start

### Requirements

- Python 3.10+
- `PySide6`
- For Image Mode and Floppy Mode: `mtools`
- For converted image formats such as HFE, and for Greaseweazle floppy import/write: Greaseweazle CLI (`gw`)
- For direct USB floppy access on Linux: a readable/writable 720K or 1.44M floppy block device

### Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6
python3 aps_midi_prep_tool.py
```

## Basic Workflow

1. Launch the app.
2. Click **Choose MIDI Folder**.
3. Review file names and extracted titles.
4. Click the **Title** column to edit entries.
5. Use **Save** to write changes, or **Save As...** for copies.
6. Use **Rename All to DOS 8.3** when targeting legacy systems.

## Website

- Main site: [alexanderpeppe.com](https://www.alexanderpeppe.com/)

## Disclaimer

This is an independent utility created for real-world piano service workflows and legacy compatibility. Test on copies first when working with important archives.
