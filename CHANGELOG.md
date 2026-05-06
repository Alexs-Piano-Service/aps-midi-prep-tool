# Changelog

All notable changes to APS MIDI Prep Tool will be recorded here.

This project follows a practical changelog format inspired by Keep a Changelog,
with release sections grouped by version and date.

## [0.6.1] - 2026-05-05

### Added

- Apache License 2.0 project license, NOTICE file, security policy, and contribution guide.
- Optional `.tags.txt` ID3 sidecar file writing for local folder saves.
- Help menu disclaimer covering backups, lawful use, copyright, and risk.
- Integrated flow for recovering damaged physical floppy disks, matching damaged image recovery.
- File menu entries for Open MIDI Folder, Open Image, and Read Floppy, matching the main window buttons.
- Yamaha Clavinova/CVP E-SEQ support for `MUSIC.DIR` directories and `.MDA` song files, including MIDI conversion and Clavinova-aware floppy/image modes.

### Changed

- Repositioned documentation around Disklavier preservation and preparation workflows.
- Updated direct floppy drive wording so internal drives are represented accurately.
- Reviewed onboarding and E-SEQ reference documentation against current app behavior.
- Expanded the E-SEQ reference with Clavinova/CVP `.MDA` and `MUSIC.DIR` findings.
- Moved tag sidecar writing into the File menu as a save behavior.
- Consolidated normal floppy reads and floppy recovery into a single Read Floppy dialog with Floppy Drive and Greaseweazle options.

### Fixed

- Direct Windows floppy writes no longer report false failures when a VM or floppy device rejects the final flush after writing completes.
- Bundled console tools launched from the GUI no longer flash black console windows on Windows.
- File-level floppy saves now leave already-matching files in place instead of deleting and copying them again, while always refreshing generated E-SEQ directory files.
- File-level floppy saves on Windows now delete old files through the mounted drive and copy final files from the temp image with mtools extended host paths, avoiding false permission-denied failures on USB and VM floppy drives.
- Windows hidden volume metadata is hidden from floppy/image listings and no longer disables fast file-level floppy reads.
- Fast floppy reads no longer fall back to full-image reads just because an otherwise readable disk has an unreadable Yamaha/protection sector in file data.
- Fast floppy reads now reconstruct readable FAT/root data from redundant sectors and stop with the recovery prompt, rather than silently starting a slow full-disk read, after a Yamaha/FAT disk has already been recognized.
- Cancelled disk reads, image conversions, Greaseweazle operations, and recovery attempts now report as cancellation instead of surfacing command or conversion errors.

## Previous Release Notes - 2026-04-30

### Added

- File Inspection opens directly from a double-click on the Type column.
- File Inspection includes piano-roll preview, channel filtering, playback position control, and bundled SoundFont support.
- Damaged image recovery can repair FAT/Yamaha structure or carve recoverable MIDI, E-SEQ, and PIANODIR data.
- New Image, Write Current Image to Floppy, Song List, update checks, and Greaseweazle drive selection persistence.

### Changed

- Recovery output now cleans damaged leading `!` characters from recovered filenames and keeps E-SEQ/PIANODIR keys consistent.
- Song List output collapses extra whitespace in album, catalog, and title text.
- AppImage builds bundle mtools, Greaseweazle, FluidSynth, and a SoundFont when available.

### Fixed

- File Inspection menu action no longer treats Qt's menu `checked` value as a selected row.
- AppImage startup prefers XCB on Linux to avoid unpredictable window resizing on some Wayland desktops.
- Clear removes the current folder context.
- Type display refreshes after staged conversion changes.
