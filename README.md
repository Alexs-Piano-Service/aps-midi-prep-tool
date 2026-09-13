<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/alexs-piano-service-logo-dark.png">
    <img src="aps_midi_prep_tool_app/assets/aps_wordmark_small.png" width="300" alt="Alex's Piano Service logo">
  </picture>
</p>

<h1 align="center">APS MIDI Prep Tool</h1>

<p align="center"><strong>Prepare music for your Disklavier and preserve piano disks</strong></p>

<p align="center">
  Edit song titles, convert MIDI and Yamaha E-SEQ, and create disks for your player piano
  or floppy emulator.
</p>

<p align="center">
  <a href="https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/releases/latest"><strong>Download the latest release</strong></a>
  · <a href="#quick-start">Quick start</a>
  · <a href="#guides-and-support">Guides</a>
  · <a href="CHANGELOG.md">Changelog</a>
</p>

Current version: `0.8.2`

APS MIDI Prep Tool is a desktop app for Yamaha Disklavier and other legacy
player-piano workflows. Use it to recover songs from old floppies, organize your
music, and prepare compatible files or disk images. A disk image is a file that
holds the contents of a floppy disk.

## Quick start

1. [Download the latest release](https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/releases/latest)
   for Windows or Linux and open the app. Packaged releases do not require a
   separate Python installation.
2. Choose **Open MIDI Folder** for MIDI or E-SEQ songs, **Open Image** for a disk
   image, or **Disk → Read Floppy...** for a physical disk. You can also drag
   files into the main window.
3. Choose **Preparing for...** to review defaults for your controller and delivery
   method. Review song titles, filenames, and playback order.
   Applying a destination stages its required conversions. Choose **Custom**
   in the destination row to return to manual preparation.
4. Choose **Save As** to export copies to a folder, or **Save As Image** to
   create IMG/HFE floppy images. **Save** updates the current source where
   supported. If the Save As folder already contains the output files, confirm
   their replacement in the overwrite prompt; this also works in the source folder.

Edits and conversions in the main list wait until you save. Formatting and
writing a physical floppy require a separate confirmation. If the floppy is
irreplaceable, make a disk image before working on it.

Use **Edit → Review Changes** to compare original and proposed filenames,
titles, and conversions or discard selected songs' edits. **Edit → Undo**
(**Ctrl+Z**) reverses the latest staged action. **Edit → Undo All** discards all
staged changes since the current files were loaded or last saved. Cancelled or
failed folder saves retain unfinished work; use **Save** again to retry it.
**View → Show Save Destination** optionally shows the save location; it is hidden
by default, and your preference is remembered.
See [review, saving, verification, and resume](docs/preparation-reliability.md)
for the complete preparation workflow.

[Preparation profiles](docs/preparation-profiles.md) use the
[APS Disklavier compatibility table](https://www.alexanderpeppe.com/disklavier-compatibility-table/)
and [PianoDisc/QRS manufacturer manuals](docs/controller-compatibility-research.md)
for controller defaults and keep emulator settings separate. Applying a profile
shows the proposed settings and stages the needed song conversions for review.
For example, Mark II preparation switches to E-SEQ mode and prepares MIDI songs
as E-SEQ with a catalog and 720 KB image defaults.
PianoDisc floppy profiles automatically prepare MIDI Type 0, including newly
imported songs and emulator disk sets. Saving stops if a song could not be
prepared for the selected controller.
The destination row highlights an active profile. **Custom** turns off automatic
preparation while keeping changes already staged for review.
**View → Show Preparation Row** controls its visibility; preparation stays active
when the row is hidden.
E-SEQ destinations disable conversion to MIDI, and MIDI destinations disable
conversion to E-SEQ, with an explanation. **Nalbantov** is available for
floppy-capable Disklavier profiles, including Mark III, and prepares HFE images
numbered from `DSKA0000.HFE`.

Change the interface language under **Settings → Language**. English, Spanish,
French, German, Italian, Brazilian Portuguese, Bulgarian, Dutch, Polish,
Japanese, Korean, and Simplified Chinese are available.

For a USB deployment, place `aps-midi-prep-tool.json` beside the executable to
preset the instrument, delivery method, saved preferences, remembered dialogs,
and folders. Relative paths follow the executable's directory, even if the USB
drive letter changes. See the [startup configuration guide](docs/startup-configuration.md)
and [Nalbantov example](docs/examples/aps-midi-prep-tool.json).

## Recover songs from a floppy or disk image

Open common IMG/BIN raw images and HFE images, or read a physical floppy with a
compatible drive. The app shows song titles, playback order, album information,
and remaining disk space. Save songs to a folder or ZIP, or create a new image.

Ordinary **Save** preserves unrelated files in an E-SEQ image, including notes,
MIDI files, and management catalogs, unless you explicitly delete them.
**Save As** and **Save As Image** still prepare a clean E-SEQ delivery set.

After three successful physical floppy reads, APS offers an optional review
invitation. **Remind me later** waits for three more successful reads;
**Never ask again** permanently dismisses it. **Write a review** opens the
review page in your browser and dismisses future invitations. These choices
are remembered across launches.

[![A Yamaha E-SEQ floppy image with editable song titles](docs/images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)](docs/images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)

Recovery tools can retry difficult disks. Greaseweazle hardware also supports
SCP archives, which capture the disk's magnetic signals for preservation.
Recovery cannot guarantee that damaged recordings will play correctly.
If USB-floppy recovery fails or is cancelled, **Disk → Save partial capture...**
keeps the recovered image together with sector coverage and diagnostics, without
reading the disk again. Unread portions are identified in the diagnostics.

For stalled image creation or a Windows `_MEI…` temporary-directory warning,
see [Windows disk troubleshooting](docs/windows-disk-troubleshooting.md).

For collections of images, **Utilities → Bulk Extraction...** can keep a local
progress record. **Resume extraction job...** retries failed items first and
reuses completed outputs only after checking input and output hashes.

## Convert and organize your music

- **Yamaha E-SEQ ↔ MIDI:** convert songs while keeping titles and playback
  order. E-SEQ exports can include the `PIANODIR.FIL` or `MUSIC.DIR` catalog
  that compatible players use to display songs.
- **MIDI Type 1 → Type 0:** combine MIDI tracks into the single-track format
  required by some older players. The app calls these formats SMF1 and SMF0.
- **Titles and filenames:** clean up title spacing, name files from song titles,
  or create the short DOS 8.3 filenames required by older hardware.
- **Playback compatibility:** optionally merge instruments onto one piano
  channel, adjust pedal behavior, or remove Yamaha XF metadata.

Conversions in the main list are ready for review before you save them. You
can apply tools to a whole folder of songs. Conversion details compare notes,
duration, channels, pedals, titles, and removed metadata. E-SEQ conversion
preserves zero-volume CC7 events by default and offers the playback fix when
the relevant condition is detected. XF cleanup preserves unrecognized metadata
and trailing data by default; broader cleanup is a separate explicit option.

<table>
  <tr>
    <td width="50%">
      <a href="docs/images/aps-midi-prep-tool-eseq-to-midi-conversion.png">
        <img src="docs/images/aps-midi-prep-tool-eseq-to-midi-conversion.png" alt="Convert Yamaha E-SEQ songs to Standard MIDI">
      </a>
    </td>
    <td width="50%">
      <a href="docs/images/aps-midi-type-1-to-type-0-conversion.png">
        <img src="docs/images/aps-midi-type-1-to-type-0-conversion.png" alt="Convert MIDI Type 1 songs to Type 0">
      </a>
    </td>
  </tr>
</table>

## Listen to songs and create audio copies

Open **Utilities → File Inspection...** to view notes, channels, instruments,
tempo, and pedals on a piano roll. Mute channels, adjust the preview mix or
tempo, and listen with a basic piano sound, a SoundFont, or a connected MIDI
device. A SoundFont supplies instrument sounds for playback.

For E-SEQ songs, **File details** includes the original header's startup tempo,
tempo factors, meter, pedal and channel flags, write protection, and display
mode. Raw values accompany their meanings, and uncertain fields remain marked
as uninterpreted. The decoded MIDI preview appears below the source details.

File Inspection also has **Convert to Type 0** and **Merge Channels to Piano**
buttons for the selected MIDI song. Each applies in one click and refreshes the
preview. Type 0 combines tracks while keeping channels and instruments; merging
routes all channels to MIDI channel 1 and selects Acoustic Grand Piano while
keeping the MIDI file type. These edits use the complete song, including hidden
or muted preview channels. Use **Save** to write them or **Edit → Undo** to revert.
**Utilities → Merge Channels to Piano...** applies the same merge to one song or
all listed MIDI songs, including files being edited inside a disk image.

Channel merging and Type 0 piano remapping translate All Notes Off (CC123) and
the related mode commands (CC124–127) into note releases for their source parts.
All Sound Off (CC120) instead requires an immediate mute, including sustained
notes and release tails. APS retains CC120 when no other source part could still
be sounding. Otherwise, it uses note releases as a lossy approximation and flags
the removed CC120 in Conversion details; sustain or release tails may continue.
Because MIDI does not specify a receiver's release duration, a part remains
potentially sounding after its note-offs until a retained immediate mute or
recognized system reset. Keep the original channels (including ordinary Type 0
conversion without piano remapping) when exact source-specific CC120 behavior
is required. See the [MIDI message definitions](https://midi.org/summary-of-midi-1-0-messages).

Use **Utilities → Render Audio...** to create WAV or MP3 copies. SoundFont
playback and rendering need optional tools listed below.

[![MIDI notes, channels, tempo, and pedals in File Inspection](docs/images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)](docs/images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)

## Create disks for a floppy emulator

Open **Utilities → Build Emulator Disk Set...** to turn a folder of MIDI or
E-SEQ songs into numbered IMG or HFE files for an emulator such as Nalbantov.
Choose the contents, image format, and capacity supported by your player.

| Layout | Use it when you want to… |
| --- | --- |
| **One album per folder** | Keep each folder's songs together. Large albums continue on extra disks; nested folders are included. |
| **Fill disks automatically** | Fill each disk before starting the next. Different folders can share a disk. |

Enable **Include Song Lists** for one text file listing every image, album, and
track in playback order. **Naming and capacity options** lets you change disk
numbering and the amount of space left free.
Review the proposed disks before writing. The preview supports album exclusions,
album order changes, title corrections, and source-to-output musical reports.

[![Options for building a numbered floppy-emulator disk set](docs/images/aps-midi-prep-tool-hfe-emulator-disk-builder.png)](docs/images/aps-midi-prep-tool-hfe-emulator-disk-builder.png)

See the [disk-set guide](docs/emulator-disk-sets.md) for folder organization,
existing song catalogs, title selection, and damaged-file handling.

The builder creates disk images. Keep the setup files from an existing emulator
USB stick and follow the manufacturer's instructions to prepare the stick.

<sub>Screenshots show the real app with self-created demonstration files.</sub>

## Protect your originals

- Make an image of an irreplaceable floppy before editing it.
- Use **File → Write Protection → Write-Protect Original** to prevent **Save**
  from overwriting the open image or floppy. You can still export copies.
- Backups are enabled by default under **File → Save Options**; an existing saved
  preference is respected. Title and order updates are checked in a temporary
  file before replacing their destination.
- Emulator disk builds reopen their final images and verify delivered file
  contents. Enable **Disk → Verify floppy contents after writing** for physical
  readback. These checks verify delivery; test playback on your player too.
- If your operating system offers to format an old piano disk, cancel that
  prompt and open the disk through APS MIDI Prep Tool instead.

## Compatibility and optional tools

The main editing workflow supports Standard MIDI (`.mid`, `.midi`), Yamaha
E-SEQ (`.FIL`, `.MDA`), and Yamaha-compatible floppy media. Disklavier E-SEQ
uses DOS 8.3 filenames, titles of up to 32 characters, and no more than 60
cataloged songs per disk.

The app also imports or converts supported PianoDisc System 3, Akai MPC,
Yamaha V50/SY77, PSR-600, and Electone MDR data. Some of these formats are
import-only or read-only conversion sources.

Some tasks need additional software or hardware. Availability varies by release
package.

| To… | You may need… |
| --- | --- |
| Work with FAT floppy images | `mtools`. |
| Use Greaseweazle hardware, convert HFE images, or capture magnetic disk signals | The Greaseweazle CLI (`gw`), plus hardware for physical disks. |
| Preview with a SoundFont or render audio | FluidSynth and a compatible SoundFont. Neither is bundled by default; basic piano preview does not need FluidSynth. |
| Create MP3 files | LAME, in addition to the audio-rendering tools above. |
| Play through a MIDI device | A connected MIDI device and `python-rtmidi`. |
| Read or write a physical floppy | A compatible drive and operating-system permission to access it. |

## Run from source

Packaged releases are the easiest way to get started. To run from source, use
Python 3.10 or newer and PySide6. On Linux, run these commands from the project
folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6
python3 aps_midi_prep_tool.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and test commands.

## Guides and support

- [Build a floppy-emulator disk set](docs/emulator-disk-sets.md)
- [Convert MIDI files and create PIANODIR.FIL](https://www.alexanderpeppe.com/eseq-and-pianodir-fil/)
- [Extract MIDI files from a Yamaha floppy disk](https://www.alexanderpeppe.com/extracting-midi-files-from-a-yamaha-floppy-disk-with-aps-midi-prep-tool/)
- [Change MIDI titles on your computer](https://www.alexanderpeppe.com/change-midi-titles-aps-midi-prep-tool/)
- [Copy a Yamaha PianoSoft floppy to a Nalbantov USB stick](https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/)
- [Convert MIDI files from Type 1 to Type 0](https://www.alexanderpeppe.com/converting-midi-files-type-1-to-type-0-aps-midi-prep-tool/)
- [Edit titles and songs in Nalbantov virtual disks](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/)

Use **Help → Send Feedback...** for suggestions or **Help → Report a Bug...**
for problems. See [CHANGELOG.md](CHANGELOG.md) for release history and
[SECURITY.md](SECURITY.md) to report a security issue.

## License and responsible use

Copyright © 2026 Alex's Piano Service LLC. Released under the
[Apache License 2.0](LICENSE).

Use only disks and files you own or are authorized to preserve, convert, or
modify. The project is independent and is not affiliated with or endorsed by
Yamaha, Disklavier, PianoSoft, PianoDisc, Nalbantov, Greaseweazle, Akai, or other
companies and products named for compatibility purposes.

[Disclaimer](https://www.alexanderpeppe.com/disclaimer/) ·
[Privacy Policy](https://www.alexanderpeppe.com/privacy-policy/) ·
[DMCA Policy](https://www.alexanderpeppe.com/dmca-policy/)
