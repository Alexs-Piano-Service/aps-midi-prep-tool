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
  · <a href="#find-the-right-guide">Guides</a>
  · <a href="CHANGELOG.md">Changelog</a>
</p>

Development version: `0.8.8`

This checkout includes changes planned for 0.8.8. The download link above points
to the latest published release; see its release notes for shipped features.

APS MIDI Prep Tool is a desktop app for Yamaha Disklavier and other legacy
player-piano workflows. Use it to recover songs from old floppies, organize your
music, and prepare compatible files or disk images. A disk image is a file that
holds the contents of a floppy disk.

## Quick start

1. [Download the latest release](https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/releases/latest)
   for Windows or Linux and open the app. Packaged releases do not require Python.
   See [installation and first use](docs/getting-started.md) for help choosing and
   opening a package.
2. Choose **Open MIDI Folder** for MIDI or E-SEQ songs, **Open Image** for a disk
   image, or **Disk → Read Floppy...** for a physical disk.
3. Choose **Preparing for...**, select your piano and delivery method, review the
   proposed settings, then choose **Apply and Prepare**.
4. Review titles, filenames, and conversions with **Edit → Review Changes...**.
   Use **Edit → Undo** (**Ctrl+Z**) to reverse the latest staged change.
5. Choose **Save As** and a new folder to export copies, or **Save As Image** to
   create an IMG/HFE image. Check the result on your player.

Edits in the song list wait until you save. **Save** updates the current source
where supported; **Save As** exports copies. For a first attempt, choose a new
output folder. Before editing an irreplaceable floppy, make a copy with
**Disk → Create Image from Floppy...**.

## Find the right guide

| I want to… | Start here |
| --- | --- |
| Open files, edit titles, or understand the save choices | [Getting started](docs/getting-started.md) |
| Choose the right format for my piano | [Preparing for your piano](docs/preparation-profiles.md) |
| Recover songs or repair an image | [Floppy and image recovery](docs/disk-recovery.md) |
| Prepare a Nalbantov or other emulator USB stick | [Build a floppy-emulator disk set](docs/emulator-disk-sets.md) |
| Listen to songs or create WAV/MP3 files | [Playback and audio](docs/playback-and-audio.md) |
| Back up a Mark IV library | [Mark IV backup](docs/mark-iv-backup.md) |
| Review conversions, retry a save, or resume extraction | [Review, save, verify, and resume](docs/preparation-reliability.md) |
| Update the app or set it up on a USB drive | [Updates](docs/self-updates.md) · [USB setup](docs/startup-configuration.md) |
| Resolve a Windows disk error | [Windows troubleshooting](docs/windows-disk-troubleshooting.md) |

[Browse all guides and technical references](docs/README.md).

## Recover songs from a floppy or disk image

Open common IMG/BIN raw images and HFE images, or read a physical floppy with a
compatible drive. Review song titles, playback order, album information, and
remaining space, then export songs to a folder or ZIP.

[![A Yamaha E-SEQ floppy image with editable song titles](docs/images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)](docs/images/aps-midi-prep-tool-yamaha-eseq-floppy-image-editor.png)

For damaged disks, image repair, partial captures, and bulk extraction, follow
the [recovery guide](docs/disk-recovery.md). Boot-sector repair changes the
selected image in place; enable its backup option to keep the original.

## Convert and organize your music

Double-click a song's **Title** or **Filename** to edit it. Use **Edit → Convert**
for song-format conversions, or choose a [preparation profile](docs/preparation-profiles.md)
to stage the conversions required by your piano.

- **Yamaha E-SEQ ↔ MIDI:** convert songs while keeping titles and playback
  order. E-SEQ exports can include the `PIANODIR.FIL` or `MUSIC.DIR` catalog
  that compatible players use to display songs.
- **MIDI Type 1 → Type 0:** combine tracks into the single-track format needed
  by some older players, using **Edit → Convert → MIDI Type 1 to Type 0**.
- **Titles and filenames:** clean up spacing, name files from song titles, or
  create the short DOS 8.3 filenames required by older hardware.
- **Playback compatibility:** optionally merge instruments onto one piano
  channel, adjust pedals, or remove Yamaha XF metadata.

Review the proposed changes before saving. Conversion reports compare notes,
duration, channels, pedals, titles, and removed metadata. See
[reviewing and saving changes](docs/preparation-reliability.md).

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

Open **Utilities → File Inspection...** to view notes on a piano roll and listen
through your computer or a connected MIDI device. Preview mix and tempo changes
leave the saved song unchanged. The dialog also offers conversion and piano-merge
buttons, which stage edits for saving.

Use **Utilities → Render Audio...** to create WAV or MP3 copies with a SoundFont.
The [playback and audio guide](docs/playback-and-audio.md) explains preview,
required tools, and overlapping-note choices when merging piano parts.

[![MIDI notes, channels, tempo, and pedals in File Inspection](docs/images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)](docs/images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)

## Create disks for a floppy emulator

Open **Utilities → Build Emulator Disk Set...** to turn a folder of MIDI or
E-SEQ songs into numbered IMG or HFE files. Choose **One album per folder** to
keep albums separate, or **Fill disks automatically** to combine songs across
folders. Review the proposed disks before building them.

[![Options for building a numbered floppy-emulator disk set](docs/images/aps-midi-prep-tool-hfe-emulator-disk-builder.png)](docs/images/aps-midi-prep-tool-hfe-emulator-disk-builder.png)

Follow the [disk-set guide](docs/emulator-disk-sets.md) for numbering, song lists,
and title choices. Keep the setup files from an existing emulator USB stick and
follow its manufacturer's setup instructions.

<sub>Screenshots show the real app with self-created demonstration files.</sub>

## Protect your originals

- Make an image of an irreplaceable floppy before editing it.
- Use **File → Write-Protect Original** to prevent **Save**
  from overwriting the open image or floppy. You can still export copies.
- Backups are enabled by default under
  **Settings → Save Options → Back Up Before Saving**; an existing saved
  preference is respected. Title and order updates are checked in a temporary
  file before replacing their destination.
- Emulator disk builds reopen their final images and verify delivered file
  contents. Enable **Settings → Disk Options → Verify Floppy Contents After Writing**
  for physical readback. These checks verify delivery; test playback on your player too.
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

Basic title editing and built-in piano preview do not need the tools below.
Some other tasks need additional software or hardware; availability varies by
release package. Windows release builds include mtools, Greaseweazle, and LAME;
standard AppImage builds include them by default. See your release notes if the
app reports a missing tool.

| To… | You may need… |
| --- | --- |
| Work with FAT floppy images | `mtools`. |
| Use Greaseweazle hardware, convert HFE images, or capture magnetic disk signals | The Greaseweazle CLI (`gw`), plus hardware for physical disks. |
| Preview with a SoundFont or render audio | FluidSynth and a compatible SoundFont. Neither is bundled by default; basic piano preview does not need FluidSynth. |
| Create MP3 files | LAME, in addition to the audio-rendering tools above. |
| Play through a MIDI device | A connected MIDI device. Source installations also need `python-rtmidi`; release builds include it. |
| Read or write a physical floppy | A compatible drive and operating-system permission to access it. |

## Guides and support

The [user guide index](docs/README.md) groups instructions by task, with separate
links for troubleshooting, advanced configuration, and technical research.

Use **Help → Send Feedback...** for suggestions or **Help → Report a Bug...**
for problems. Include what you were doing, the exact error, and your app version
from **Help → About APS MIDI Prep Tool**. On Windows, start with the
[disk troubleshooting guide](docs/windows-disk-troubleshooting.md) for disk errors.

Use **Help → Check for Updates...** to look for a new release. See
[self-updates and recovery](docs/self-updates.md) for supported packages.
[CHANGELOG.md](CHANGELOG.md) records release history;
[SECURITY.md](SECURITY.md) explains how to report a security issue.

## Run from source

Packaged releases are the easiest way to get started. To run from source, use
Python 3.10 or newer, PySide6, and certifi. On Linux, run these commands from the project
folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6 certifi
python3 aps_midi_prep_tool.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and test commands.

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
