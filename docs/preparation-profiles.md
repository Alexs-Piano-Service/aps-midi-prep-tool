# Preparing for your piano

Choose **Preparing for...** above the song list, or in **Settings**. Controllers
are grouped under **General**, **Disklavier**, **PianoDisc**, and **QRS**. Select
the controller and delivery method, review **Current → Proposed**, then choose
**Apply and Prepare**. Required MIDI/E-SEQ conversions are staged, the editing
mode changes, and future imports follow that destination. For Mark II, the list
switches to E-SEQ and prepares PIANODIR.FIL with 720 KB defaults. Original files
are written only when you save. **Edit → Undo** (**Ctrl+Z**) restores the previous
profile and staged state after applying a destination. **Edit → Undo All**
discards all staged changes since the current files were loaded or last saved.
Use **Edit → Review Changes** to inspect proposed filenames, titles, and formats.

Choices are indented only in the open controller list; the selected controller
stays aligned normally, and typing a model name selects it. Active preparation
also applies the required song format and, where required, MIDI Type 0 when
adding or replacing songs in an image and when building emulator disk sets.
If a file cannot be prepared, saving and export stop until it is prepared or
removed, or you choose **Custom**.

The destination row highlights active preparation. Click **Custom** in that row
to turn off automatic preparation and allow conversion to either format. Changes
already staged stay available for review. **Custom** and **I'm not sure** keep
manual settings.

Use **View → Show Preparation Row** to hide or show that row. Hiding it keeps
the selected preparation active; **Settings → Preparing for...** remains available.
The preparation dialog links to the selected controller's evidence: the **APS
Disklavier Compatibility Table** for Disklavier and General choices, or the
**PianoDisc user manual** or **QRS user manual** for those manufacturers.

Floppy and emulator preparation also stages unique DOS 8.3 names for songs whose
current filenames are incompatible. Existing valid names remain unchanged;
USB and app folder preparation keep descriptive filenames.
Clavinova MDA songs are staged as Disklavier FIL through a MIDI intermediate;
the review identifies this container conversion. Files that cannot be converted
remain visible as unprepared, with an error, and their originals stay intact.

The controller determines song format and disk capacity. Emulator configuration
separately determines image type and numbering. Defaults feed **New Image**,
**Save As Image**, and **Build Emulator Disk Sets**. Selecting an HFE delivery
starts those dialogs in HFE even when an open source image is IMG or a different
format was used previously. You can change the format in the export dialog.
An **Unsure** controller can still select a generic delivery format without
assuming a song format or disk capacity. Normal **Save** retains the original
image format; capture and recovery options retain their own settings.
USB and PianoDisc app preparation use **Save As** to
export a folder. For QRS USB profiles, copy the MIDI files to a USB drive and
select it on the controller. For Prodigy, import the exported MIDI files into
the PianoDisc iQ Player app and use its supported connection to the piano.
Review the staged result before saving. Fresh MIDI-to-Disklavier E-SEQ conversion
automatically generates Yamaha's channel-1 binary and channel-3 continuous pedal
layers; see the [conversion policy](mid2eseq-compatibility.md). Instrument merging,
the separate Pedal Compatibility utility, zero-volume fixes, and broad metadata
cleanup remain separate choices.

Disklavier capabilities follow the [APS compatibility table](https://www.alexanderpeppe.com/disklavier-compatibility-table/),
reviewed September 7, 2026. Early Mark I and uncertain Mark II units default to
E-SEQ on 720 KB disks. DSR1, Mark II XG, and Mark III default to MIDI on 1.44 MB.
Mark IV, E3, DKC-850, and ENSPIRE default to MIDI folder export. ENSPIRE/DKC-900
requires converting E-SEQ to MIDI; E3 and DKC-850 can play E-SEQ. Unit-dependent
Mark II MIDI support stays explicit in the guidance. Keep one song format per
floppy.

PianoDisc and QRS profiles add these destinations, based on manufacturer manuals
reviewed September 8, 2026:

| Group | Controller | Prepared format | Default delivery |
| --- | --- | --- | --- |
| PianoDisc | PDS-128 Plus | MIDI Type 0 | 720 KB floppy |
| PianoDisc | PDS-228CFX / SilentDrive | MIDI Type 0 | 720 KB floppy |
| PianoDisc | Prodigy / Prodigy II (iQ Player) | MIDI | App folder export |
| QRS | Chili / AMC | MIDI Type 0 or Type 1 | 1.44 MB floppy |
| QRS | PNOmation II / PMII | MIDI | USB folder export |
| QRS | PNO3 | MIDI | USB folder export |
| QRS | PNO4 / PNO4 Touch | MIDI | USB folder export |

The two PianoDisc floppy profiles automatically stage Type 1 files as Type 0,
combining MIDI tracks while retaining channels, instruments, and event timing.
Both controllers also support HD media; 720 KB is the preparation default.
Chili's 1.44 MB default follows its documented DOS HD media and photographed
3.5-inch drive. The modern QRS and Prodigy manuals do not specify SMF types, so
their profiles do not impose a Type 0 conversion.

Folder export prepares ordinary MIDI files. It does not create PianoDisc encoded
audio, native System 3 disks, secured QRS music, or CompactFlash media layouts.
The original PDS-128 and original iQ audio controller are not covered by the
similarly named Plus and Prodigy profiles. See the
[controller evidence and limitations](controller-compatibility-research.md) for
manufacturer links and page references.

**Nalbantov** is available for floppy-capable **Disklavier** profiles, including
Mark I and Mark III. Use the single Nalbantov option to prepare HFE images.
For another installed emulator, use its preset or
**Other emulator / manual configuration**.
PianoDisc and QRS floppy profiles offer the original drive or manual emulator
configuration.

Nalbantov and Gotek/FlashFloppy presets start at `DSKA0000`.
Nalbantov images are named `DSKA0000.HFE`, `DSKA0001.HFE`, and so on; see the
[APS Nalbantov workflow](https://www.alexanderpeppe.com/adding-removing-or-changing-titles-in-nalbantov-usb-stick-virtual-disks/).
Keep the stick's configuration files.

FlashFloppy indexed profiles require the emulator's indexed navigation mode.
See [FlashFloppy navigation](https://github.com/keirf/flashfloppy/wiki/Image-Navigation-Modes)
and [supported image types](https://github.com/keirf/flashfloppy/wiki/Image-Formats).
Use **Other emulator / manual configuration** for a different firmware or naming
scheme. The app does not configure the emulator's drive interface.

Profiles distinguish documented requirements from unverified configurations.
None claims hardware playback testing. Generated files still need checking on
the actual controller, firmware, and drive combination.
