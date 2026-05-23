# Changelog

All notable changes to APS MIDI Prep Tool will be recorded here.

This project follows a practical changelog format inspired by Keep a Changelog,
with release sections grouped by version and date.

## [0.6.5] - 2026-05-19

### Added

- View menu with `Long title warning`, `Format for Disklavier screen`, `Hide Status`, `Hide Quick Panel`, `Hide Album Info`, and `View Logs...`.
- Live console log window with realtime stdout/stderr capture, search, pause, follow, copy, save, and clear controls.
- Disk menu that groups floppy/image media actions: `Read Floppy...`, `Image Floppy...`, `Save To Floppy...`, `Write Current Image to Floppy...`, recovery, and format tools.
- File menu submenus for `Save Options` and `Write Protection`, including `Create Album Subfolder`, `Back up before Saving`, `Write-Protect Original`, tag sidecars, and metadata summaries.
- Default keyboard shortcuts for the current File, Disk, View, Utilities, Settings, and Help menu commands.
- Optional `Do not show this dialog again` choice for Save As Image completion messages.
- `Trim Title Spaces` utility and hotkey, plus a Read Floppy option to clean Disklavier-spaced titles after normal or Greaseweazle reads.
- `Help > Report a Bug...` action with a support-report dialog that sends app context and optional recent console output.
- `Report This Bug...` button on unexpected operation-failure dialogs, prefilled with the error message and recent logs enabled.
- Empty file-list overlay text plus drag-hover highlighting for supported file drops.
- Bulgarian language support across the language selector, menus, dialogs, common workflows, and fallback catalog coverage.
- SoundFont picker and manager in File Inspection for choosing local SoundFonts or downloading SoundFonts from the app's online catalog, including recommended/category details and automatic unpacking for common archives.
- `Utilities > Render Audio...` batch renderer for exporting all listed MIDI or E-SEQ files as WAV or MP3 using a selected SoundFont.

### Changed

- Welcome workflows and README guidance now reflect the current menu labels and safety options.
- Menus were reorganized so File focuses on source/save behavior, Disk focuses on floppy and media operations, and Utilities focuses on inspection and batch conversion tools.
- Album Title and Catalog Number remain visible by default for Save As album-folder workflows, can be hidden from View, and refresh or blank when a disk is read.
- `Create Album Subfolder` is treated as part of the Album Info panel in the quick panel, and Save As now states whether it used the album subfolder or saved directly in the selected folder.
- Save As folder-export language now clarifies that album subfolders never affect Save As Image or floppy writes.
- Image and floppy save confirmation wording now describes renamed files as updates rather than removals.
- Write-protect wording is consistently hyphenated as `Write-Protect Original`.
- SoundFont dropdowns now use catalog names and clearer format/source labels instead of filename-derived labels when possible.

### Fixed

- Save warnings shown during a single-file image rename no longer imply the file is being removed.
- New log-window and save-confirmation dialog text now participates in the language catalog.
- Greaseweazle sector maps now mark a blank first sector as possible Yamaha copy protection instead of reporting it as damage that needs attention.
- Archival Greaseweazle reads now show one logical read sector-map dialog after the raw capture conversion.
- Greaseweazle progress now stays determinate when a blank first sector produces extra status output.
- App-owned dialogs and progress windows now recenter on the APS MIDI Prep Tool window when shown.
- Greaseweazle read sector-map dialog text is now shorter, with a localized compact color legend.
- Greaseweazle sector-map dialogs now show a polished visual legend with colored markers.
- Opening a saved Greaseweazle read no longer shows a second conversion sector-map dialog after the read map.
- Greaseweazle retry chatter now renders as a steady progress message instead of rapidly changing dialog text.
- Greaseweazle first-track possible Yamaha copy-protection progress now stays stable while sector retries are reported.
- Greaseweazle image-save defaults now use the disk catalog number, stripped to filename-safe letters and numbers, when available.
- The Greaseweazle read progress dialog now explicitly shows and recenters after its first progress updates.
- Save As Image no longer shows a Greaseweazle conversion sector map after a Greaseweazle disk read.
- Greaseweazle read sector maps now show after a disk read when available, with the existing `Do not show` preference still available.
- Save As Image now keeps progress visible while reopening a newly saved HFE or other converted image, avoiding a blank apparent hang after export.
- The immediate post-read Greaseweazle HFE save now writes the just-read capture directly instead of applying repairs or staged title edits meant for a later explicit Save As Image.
- Modal dialogs and progress windows now recenter when their contents resize, including Greaseweazle read messages and possible Yamaha copy-protection notes.
- Album Title and Catalog Number now remain populated when saving/exporting a disk session switches the app back to MIDI Mode.
- `Trim Title Spaces` now refreshes immediately after manual title edits, and the Disklavier screen title editor now shows the existing 16-character title lines directly without adding automatic padding.
- Drag-and-drop now accepts Windows file drags without pre-sniffing paths, contains path/probing failures during drop processing, and closes the Adding Files dialog cleanly instead of hanging.
- View Logs now uses Python stream capture on Windows instead of descriptor-level capture, improving reliability for PowerShell/Qt error output.
- Folder/file importing now skips unreadable Windows paths during probing instead of aborting the whole import.
- Formatting a USB floppy now reuses an already matching IBM FAT format when possible, clearing files and adding an empty `PIANODIR.FIL` for E-SEQ without rewriting the whole disk.
- The drag-and-drop overlay now keeps the supported-file subtitle consistent while files are being dragged.
- The drag-and-drop overlay subtitle and dashed outline now use higher-contrast colors for better Windows theme visibility.
- Blank or unformatted HFE images are now identified after the first matching conversion attempt, with a clear blank-image message instead of trying every disk geometry and offering recovery.
- Logs now use consistent timestamped, human-readable entries and include high-level app events for folder/image/floppy reads, saves, conversions, drag/drop, bug reports, settings changes, warnings, and failures.
- Release bundles no longer include FluidSynth by default, while AppImage and Windows release builds include LAME for MP3 export when available.

## [0.6.1] - 2026-05-05

### Added

- Apache License 2.0 project license, NOTICE file, security policy, and contribution guide.
- Optional `.tags.txt` ID3 sidecar file writing for local folder saves.
- Help menu disclaimer covering backups, lawful use, copyright, and risk.
- Integrated flow for recovering damaged physical floppy disks, matching damaged image recovery.
- File menu entries for Open MIDI Folder, Open Image, and Read Floppy, matching the main window buttons.
- File menu option for imaging a physical floppy directly to IMG or SCP without opening or scanning the disk contents.
- Utility for formatting removable USB sticks as FAT32 superfloppies for Yamaha E3/ENSPIRE Disklaviers or as MBR single-partition FAT32 disks for PianoForce, with device preview and destructive-action warnings.
- File menu option to create `metadata_summary.txt` on save, listing saved MIDI files and their detected metadata.
- Greaseweazle sector-map PNG previews after successful Greaseweazle reads, writes, and image conversions, with separate hide preferences for each transaction type; routine HFE-to-IMG opening skips the preview.
- HFE image opening now prefers 720K conversion for roughly 2 MB HFE files and 1.44M conversion for roughly 4 MB HFE files before trying other formats.
- Greaseweazle image-only conversion can recognize Macintosh 800K GCR/HFS SCP captures and save decoded IMG files without trying to open them as Yamaha FAT disks.
- Akai MPC `.ALL` sequence extraction from dropped files, opened files, disk images, and selected folders.
- Yamaha V50/SY77 sequence extraction when the V50/SY77 signature is present.
- Yamaha Electone MDR disk reading, including `.VFD` raw images and MDR images with blank or nonstandard boot sectors, plus `.EVT` performance conversion to Standard MIDI.
- Yamaha Clavinova/CVP E-SEQ support for `MUSIC.DIR` directories and `.MDA` song files, including MIDI conversion and Clavinova-aware floppy/image modes.
- Centralized localized message catalog with language selection, translated common dialogs, and reusable guidance for Greaseweazle, permission, write-protection, disk-full, unsupported-image, FAT/boot-sector, and cancellation errors.
- Settings menu with language selection, System/Light/Dark appearance options, and a reset action for hidden warning, confirmation, update-reminder, and Greaseweazle sector-map dialogs.

### Changed

- Repositioned documentation around Disklavier preservation and preparation workflows.
- Clarified the app's broader format direction: Disklavier preparation remains the
  primary purpose, while the tool is gaining basic understanding of related floppy
  formats that regularly appear in preservation work, including other Yamaha
  E-SEQ variants, V50/SY77 sequence disks, Electone MDR disks, and Akai MPC media.
- Updated direct floppy drive wording so internal drives are represented accurately.
- Reviewed onboarding and E-SEQ reference documentation against current app behavior.
- Expanded the E-SEQ reference with Clavinova/CVP `.MDA` and `MUSIC.DIR` findings.
- Moved tag sidecar writing into the File menu as a save behavior.
- Consolidated normal floppy reads and floppy recovery into a single Read Floppy dialog with Floppy Drive and Greaseweazle options.

### Fixed

- Recovery mode now continues scanning partially converted Greaseweazle images when conversion reports bad sectors, especially when the user selected an explicit disk format.
- Damaged image recovery now shows the Greaseweazle good/bad sector-map preview when recovery succeeds with bad or missing sectors.
- Disk recovery dialogs now remember the last recovery mode and selected recovery disk formats.
- Save As and Save As Image now reopen in the last successful save destination.
- Greaseweazle sector-map hide choices are reset again so recovery sector charts are not accidentally suppressed.
- Recovery now shows available Greaseweazle sector maps even when all sectors converted cleanly, and reports when a raw image has no sector map to chart.
- Each disk recovery run now resets sector-map duplicate tracking so repeated recoveries can show their chart again.
- Recovery Complete and E-SEQ to MIDI conversion confirmation dialogs now include hide-this-dialog checkboxes.
- Macintosh 800K SCP detection now runs only after IBM/Yamaha conversions fail with zero readable sectors, avoiding eager Mac probing for damaged Yamaha captures.
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
