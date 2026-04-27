# APS MIDI Prep Tool

`APS MIDI Prep Tool` is a desktop utility for preparing MIDI, Yamaha E-SEQ, floppy disk, and Nalbantov virtual disk workflows for Disklaviers and other legacy player-piano systems.

Current version: `0.5.2` (2026).

The tool was built for the real-world workflows documented at [AlexanderPeppe.com](https://www.alexanderpeppe.com/): extracting songs from Yamaha floppies, cleaning up MIDI titles, converting SMF1 files to SMF0, copying PianoSoft disks to Nalbantov USB sticks, and editing HFE virtual floppy images.

## What It Helps With

Older Disklavier and player-piano systems can be picky about file formats, internal titles, filenames, and floppy image structure. APS MIDI Prep Tool focuses on practical compatibility work:

- Edit internal MIDI titles, not just filenames.
- Keep titles short and readable for older screens.
- Convert MIDI Type 1 / SMF1 files to Type 0 / SMF0.
- Convert between Yamaha E-SEQ `.FIL` files and standard MIDI `.MID` files.
- Generate or refresh `PIANODIR.FIL` for Yamaha E-SEQ sets.
- Rename MIDI files to DOS 8.3 style when needed.
- Read real Yamaha floppies through USB floppy drives or Greaseweazle.
- Open, edit, and save floppy images such as Nalbantov `.hfe` files.
- Stage changes first, then write them with `Save`, `Save As`, or `Save As Image`.

## Pick Your Workflow

Start with the job you are actually trying to finish:

### Change MIDI Titles or Filenames

Use this when you already have `.mid` or `.midi` files on your computer.

1. Click `Open MIDI Folder`, or drag MIDI files into the window.
2. Edit the `Title` column if the piano shows blank, awkward, or wrong names.
3. Use `Rename 8.3` if the target system needs short filenames.
4. Use `Save` to update the listed files, or `Save As` to write copies.

Full walkthrough: [How to Change MIDI Titles on Your Computer Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/change-midi-titles-aps-midi-prep-tool/)

### Convert MIDI Type 1 to Type 0

Use this when MIDI files play on a computer but behave badly on a piano, such as missing one hand or ignoring accompaniment.

1. Open a MIDI folder or drag MIDI files into the window.
2. Check the `Type` column.
3. Click `SMF1 -> SMF0`.
4. Confirm the table now shows `Type 0`.
5. Use `Save` to overwrite originals, or `Save As` to write converted copies.

The conversion is staged first. The original files are not changed until you save.

Full walkthrough: [Converting MIDI Files From Type 1 to Type 0 Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/converting-midi-files-type-1-to-type-0-aps-midi-prep-tool/)

### Extract MIDI Files from a Yamaha Floppy

Use this when you have a physical Yamaha or Disklavier floppy and want normal files on your computer.

1. Insert the floppy. If the OS asks to format it, cancel.
2. Click `Read Floppy`.
3. Review the files and the `Type` column.
4. If the disk contains E-SEQ files, click `E-SEQ -> MIDI`.
5. Optionally clean up titles.
6. Use `Save As` to write the extracted files to a folder.

For preservation, prefer `Save As` over writing back to the original floppy.

Full walkthrough: [Extracting MIDI Files from a Yamaha Floppy Disk with APS MIDI Prep Tool](https://www.alexanderpeppe.com/extracting-midi-files-from-a-yamaha-floppy-disk-with-aps-midi-prep-tool/)

### Copy a PianoSoft Floppy to a Nalbantov USB Stick

Use this when you want a Nalbantov-compatible virtual floppy image from an original Yamaha PianoSoft disk.

1. Insert the original floppy.
2. Click `Read Floppy`.
3. Review the contents.
4. Use `Save As Image`.
5. Choose an HFE output for the Nalbantov USB stick.
6. Keep a computer backup of the image too.

Full walkthrough: [Copying a Yamaha PianoSoft Floppy Disk to a Nalbantov USB Stick](https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/)

### Edit a Nalbantov HFE Virtual Disk

Use this when you already have a Nalbantov `.hfe` file and need to change what is inside it.

1. Click `Open Image`, or drag the `.hfe` file into the window.
2. Review the files inside the virtual floppy.
3. Drag MIDI or E-SEQ files into the table to stage additions.
4. Click `X` to stage removals.
5. Edit filenames or titles if needed.
6. Use `Save` to update the image, or `Save As Image` to create a new image.

Full walkthrough: [Adding, Removing, or Changing Titles in Nalbantov USB Stick Virtual Disks](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/)

## Important Concepts

### MIDI Title vs Filename

The filename is what your computer shows, such as `MYSONG.MID`. The MIDI title is metadata inside the file. Many Disklavier and player-piano systems display the internal title, so changing only the filename may not change what appears on the piano.

### MIDI vs E-SEQ

`.MID` files are standard MIDI files. Yamaha E-SEQ files are commonly stored as `.FIL` files on older Disklavier and PianoSoft disks. APS MIDI Prep Tool can convert between these formats.

When you are in E-SEQ mode and drag in MIDI files, the tool stages MIDI-to-E-SEQ conversion automatically. When you are in MIDI mode and drag in E-SEQ files, it stages E-SEQ-to-MIDI conversion automatically.

### PIANODIR.FIL

`PIANODIR.FIL` is Yamaha's E-SEQ song directory/index file. It is not a song. The tool shows it separately and can generate or refresh it when saving an E-SEQ set or image.

### Save, Save As, and Save As Image

- `Save` writes pending changes back to the current files, image, or floppy session.
- `Save As` writes the listed files to a folder.
- `Save As Image` creates a floppy image from the current list or session.

When working from original disks or important archives, use copies and prefer `Save As` until you have tested the result.

## Features

- Load `.mid` and `.midi` files from a folder.
- Drag and drop MIDI, E-SEQ, and supported image files.
- Click titles to edit and queue changes.
- Optional warning for titles longer than 32 characters.
- Optional Disklavier screen formatting for two 16-character title rows.
- One-click `Rename 8.3`.
- Staged `SMF1 -> SMF0` conversion.
- Image Mode for `.img`, `.hfe`, and other Greaseweazle-supported formats.
- Floppy Mode for 720K and 1.44M disks.
- Linux fast USB floppy reads when possible, including many Yamaha 720K disks with a blank or corrupt sector 0.
- Greaseweazle floppy read/write support, including drive selection.
- Yamaha E-SEQ title editing, order handling, and `PIANODIR.FIL` management.
- Optional backups before overwriting files or images.

## Compatibility Notes

For older Disklavier and floppy-style workflows, these practices are usually safest:

- Keep titles short, plain, and readable.
- Prefer printable ASCII characters.
- Use DOS 8.3 filenames when the destination system expects them.
- Convert SMF1 to SMF0 only when the target system needs it.
- Test converted files on the actual playback system.
- Do not format a Yamaha floppy just because your computer says it is unreadable.

The SMF1-to-SMF0 conversion merges tracks into one track. It does not remap MIDI channels. Yamaha XG files may need special care, so test on copies.

## Requirements

- Python 3.10+
- `PySide6`
- For source/local runs with Image Mode and Floppy Mode: `mtools`
- For source/local runs with HFE, Greaseweazle floppy import, or Greaseweazle floppy write: Greaseweazle CLI (`gw`)
- For direct USB floppy access on Linux: a readable/writable 720K or 1.44M floppy block device

Release AppImages built with the included script bundle the needed `mtools` commands and the Greaseweazle CLI. Direct USB floppy access still depends on normal Linux device access and permissions.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6
python3 aps_midi_prep_tool.py
```

## Build a Release AppImage

On Linux, run the included build task to create a single-file AppImage:

```bash
make appimage
```

You can also run the default VS Code task, **Build AppImage**.

The task creates `release/APSMidiPrepTool-<version>-<arch>.AppImage`, for example `release/APSMidiPrepTool-0.5.2-x86_64.AppImage`. Upload that file as the release artifact. The first build creates `.venv-appimage/`, installs the build requirements, downloads `appimagetool`, and packages the PySide6 app with PyInstaller.

By default, the AppImage build also bundles:

- `mformat`, `mcopy`, `mdel`, `mren`, and `mdir` from `mtools`
- Greaseweazle CLI as `gw`

The build machine needs `mtools`, `git`, and network access for the default bundle. To skip either bundled dependency, run:

```bash
BUNDLE_MTOOLS=0 make appimage
BUNDLE_GREASEWEAZLE=0 make appimage
```

To pin Greaseweazle to a specific source or revision, set `GREASEWEAZLE_REQUIREMENT`, for example:

```bash
GREASEWEAZLE_REQUIREMENT='git+https://github.com/keirf/greaseweazle.git@v1.23' make appimage
```

For the widest Linux compatibility, build releases on an older supported distro, such as Ubuntu 22.04.

## Related Guides

- [Extracting MIDI Files from a Yamaha Floppy Disk with APS MIDI Prep Tool](https://www.alexanderpeppe.com/extracting-midi-files-from-a-yamaha-floppy-disk-with-aps-midi-prep-tool/)
- [How to Change MIDI Titles on Your Computer Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/change-midi-titles-aps-midi-prep-tool/)
- [Copying a Yamaha PianoSoft Floppy Disk to a Nalbantov USB Stick](https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/)
- [Converting MIDI Files From Type 1 to Type 0 Using APS MIDI Prep Tool](https://www.alexanderpeppe.com/converting-midi-files-type-1-to-type-0-aps-midi-prep-tool/)
- [Adding, Removing, or Changing Titles in Nalbantov USB Stick Virtual Disks](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/)

## Disclaimer

This is an independent utility created for piano service, preservation, and legacy compatibility workflows. Work on copies when possible, keep backups, and use it only with disks and files you have the right to preserve or modify.
