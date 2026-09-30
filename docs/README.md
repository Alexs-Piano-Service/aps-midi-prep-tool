# APS MIDI Prep Tool guides

[Project overview and download](../README.md) · [Release notes](../CHANGELOG.md)

Start with [Getting started](getting-started.md) to open songs, edit titles, and
save your first copies. These guides describe the development checkout; the
[README](../README.md) identifies its version. If a control is missing from your
installed release, check that release's notes and **Help → Check for Updates...**.

## Prepare and use your music

| Task | Guide |
| --- | --- |
| Install the app, open files or ZIPs, and choose a save command | [Getting started](getting-started.md) |
| Choose a piano, song format, or delivery method | [Preparing for your piano](preparation-profiles.md) |
| Review conversions, undo edits, or retry unfinished saves | [Review, save, verify, and resume](preparation-reliability.md) |
| Read a floppy, repair an image, or extract a collection | [Floppy and image recovery](disk-recovery.md) |
| Build numbered images for Nalbantov or another emulator | [Build a floppy-emulator disk set](emulator-disk-sets.md) |
| Listen to songs, merge piano parts, or create WAV/MP3 copies | [Playback and audio](playback-and-audio.md) |
| Copy and verify a Mark IV music library | [Mark IV backup](mark-iv-backup.md) |

## Setup and troubleshooting

| Task or symptom | Guide |
| --- | --- |
| Change language, text size, or shortcuts | [Customize the interface](getting-started.md#customize-the-interface) |
| A feature reports missing software | [Compatibility and optional tools](../README.md#compatibility-and-optional-tools) |
| Update a supported EXE/AppImage or restore a previous version | [Self-updates and recovery](self-updates.md) |
| Preset the app for a USB drive or another user | [Startup configuration](startup-configuration.md) |
| A Windows disk operation fails or stalls | [Windows disk troubleshooting](windows-disk-troubleshooting.md) |
| A filename rename was interrupted | [Rename recovery](rename-recovery.md) |
| A floppy read or bulk extraction stopped partway through | [Preserve recovery and extraction work](preparation-reliability.md#preserve-recovery-and-extraction-work) |

For a problem you cannot resolve, choose **Help → Report a Bug...**. Include the
task, the exact error, and the app version from **Help → About APS MIDI Prep Tool**.
Use **Help → Send Feedback...** for suggestions.

## Illustrated website tutorials

These articles provide additional walkthroughs. Screenshots and menu positions
may reflect an earlier release; use the guides above for the current workflow.

- [Convert MIDI files and create PIANODIR.FIL](https://www.alexanderpeppe.com/eseq-and-pianodir-fil/)
- [Extract MIDI files from a Yamaha floppy disk](https://www.alexanderpeppe.com/extracting-midi-files-from-a-yamaha-floppy-disk-with-aps-midi-prep-tool/)
- [Change MIDI titles on your computer](https://www.alexanderpeppe.com/change-midi-titles-aps-midi-prep-tool/)
- [Copy a Yamaha PianoSoft floppy to a Nalbantov USB stick](https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/)
- [Convert MIDI files from Type 1 to Type 0](https://www.alexanderpeppe.com/converting-midi-files-type-1-to-type-0-aps-midi-prep-tool/)
- [Edit titles and songs in Nalbantov virtual disks](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/)

## Technical references and research

These explain format behavior and compatibility evidence. They are not required
for ordinary preparation. Some research notes refer to local recordings or
analysis files that are not distributed with the repository.

- [PianoDisc and QRS controller evidence](controller-compatibility-research.md)
- [Yamaha E-SEQ format reference](../aps_midi_prep_tool_app/eseq_reference.md)
- [MID2ESEQ conversion compatibility](mid2eseq-compatibility.md)
- [E-SEQ timing verification](eseq-timing-verification.md)
- [Mark II conversion comparison](mark-ii-elton-investigation.md)
- [Official Yamaha album comparison](official-album-comparison.md)
- [Mark IV drive and pedal-conversion investigation](mark-iv-drive-investigation.md)
- [Old/new disk MIDI playback comparison](live-midi-old-new-comparison.md)

## Development and release maintenance

- [Run from source](../README.md#run-from-source) and [contribute changes](../CONTRIBUTING.md)
- [Release packages and acceptance evidence](release-process.md)
- [Optional GitHub bug-report forwarding](bug-report-github.md)
- [Windows release acceptance tests](windows-test-plan.md)
- [SoundFont catalog and packaging reference](../aps_midi_prep_tool_app/soundfonts/README.md)
- [Report a security issue](../SECURITY.md)
