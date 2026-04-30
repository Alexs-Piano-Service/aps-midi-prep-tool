# Changelog

All notable changes to APS MIDI Prep Tool will be recorded here.

This project follows a practical changelog format inspired by Keep a Changelog,
with release sections grouped by version and date.

## [Unreleased]

### Added

- Apache License 2.0 project license, NOTICE file, security policy, and contribution guide.
- Optional `.tags.txt` ID3 sidecar file writing for local folder saves.
- Help menu disclaimer covering backups, lawful use, copyright, and risk.
- Utilities flow for recovering damaged physical floppy disks, matching damaged image recovery.

### Changed

- Repositioned documentation around Disklavier preservation and preparation workflows.
- Updated direct floppy drive wording so internal drives are represented accurately.
- Reviewed onboarding and E-SEQ reference documentation against current app behavior.
- Moved tag sidecar writing out of Utilities and into the Options area.

## [0.5.3] - 2026-04-30

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
