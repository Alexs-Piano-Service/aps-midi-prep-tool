# Preparing for your piano

[All user guides](README.md) · [Getting started](getting-started.md)

Use a preparation profile to choose the song format and delivery defaults for
your piano's controller. Changes are **staged**: they remain pending in the app
until you save or export them.

## Start here

1. Choose **Preparing for...** above the song list, or **Settings → Preparing for...**.
2. Select your controller under **Preparing for** and your **Delivery** method. Controllers are
   grouped under **General**, **Disklavier**, **PianoDisc**, and **QRS**; typing a
   model name selects it. If you do not know your model, choose **I'm not sure**
   and check the [controller defaults](#controller-defaults) before converting songs.
3. Check the outcome summary: the song count, delivery type, and confirmation that
   originals remain unchanged. Open **Details** to compare **Saved settings**
   with **On apply**, review conversion counts, or follow compatibility references.
   Choose **Apply** to stage preparation.
4. Open **Edit → Review Changes...** to inspect proposed filenames, titles,
   formats, and conversion reports. Use **Edit → Undo** (**Ctrl+Z**) to restore
   the previous profile and staged state if needed.
5. Choose the save or export command for your [delivery method](#choose-a-delivery-method).

The summary counts the loaded songs and describes the selected output. **Details**
labels the preference comparison **Saved settings / On apply**; saved preferences
do not describe the formats of the loaded songs. Image numbering, preparation
notes, and compatibility references are also inside **Details**.

The main window keeps the destination controls compact. The preparation dialog
shows the song count and any songs that need attention. Saving and export check
the selected destination requirements and list affected files if preparation
is incomplete.

Applying a profile prepares loaded songs and changes the editing mode; future
imports follow the same destination. For Mark II, the list switches to E-SEQ
with 720 KB defaults, and saving creates its `PIANODIR.FIL` song catalog.
Preparation also applies when adding or replacing songs in an image and when
building emulator disk sets, including MIDI Type 0 conversion where required.
If a song cannot be prepared, saving and export stop until it is prepared or
removed, or you choose **Custom**. Its original file stays intact.

**Edit → Undo All** discards all staged changes since the current files were
loaded or last saved. See [reviewing and saving changes](preparation-reliability.md)
for the differences between Save, Save As, and Save As Image.

## Change or turn off automatic preparation

Preparation keeps three layers: the loaded songs, your manual edits, and the
automatic changes required by the current destination. Choosing a different
destination replaces the automatic layer. It preserves title edits, filenames,
song order, additions, removals, and deliberate conversions.

While a destination is active, MIDI tools work on the retained MIDI source
before automatic preparation is applied again. For example, a pedal edit preserves the original track layout
even when the destination requires Type 0. A MIDI-only edit to an original E-SEQ
song retains the MIDI conversion needed for that explicit edit.

For example, choosing PianoDisc 128Plus and then Standard MIDI export restores
the original Type 1 organization, unless you explicitly converted it yourself.
Choosing an E-SEQ destination and then a MIDI destination prepares from the
retained source, without a conversion round trip. Choosing B directly produces
the same result as choosing A and then B when you make no edits between them.

The destination row highlights active preparation. **Custom** means manual mode:
stop automatic preparation and keep the current work and settings. Clicking
**Custom** in that row or applying **Custom** in the preparation dialog does
exactly the same thing. Prepared conversions, titles, filenames, ordering, and
all other staged work stay as they are. Filename, title-formatting, and export
preferences also stay unchanged, and you can edit the previously locked options
or convert songs to either format.
In Custom, tools edit the current staged songs; those deliberate edits remain
part of your work when preparation is reset.

To remove automatic preparation changes, use **Edit → Reset preparation**.
This turns off automatic preparation and preserves deliberate manual edits and
current preferences. It works after entering **Custom**, too. **Edit → Undo**
restores the previous preparation and mode. Neither action changes the originals.

Choosing another destination later still replaces the previous automatic
preparation, including when you used **Custom** in between. **I'm not sure**
remains a separate dialog choice with proposed defaults for review.

Saving changes to the loaded files commits those songs as the new source.
If only some files save successfully, unsaved songs keep their reversible
preparation. When Save As opens the saved files or image, that result becomes
the new source; the previous originals remain unchanged. ZIP export keeps the
current loaded songs and preparation.

Options controlled by the destination are disabled and show their effective
values. Each affected dialog has one preparation note, and disabled controls
explain their setting in a tooltip. For MIDI destinations, **Convert E-SEQ files
to MIDI after reading** is checked and locked; for E-SEQ destinations it is
unchecked and locked. Required filename rules, title cleanup, and song-format
choices follow the same convention. Choose **Custom** to edit these options.
Image type, capacity, and screen formatting remain editable defaults.

The preparation row is visible by default. Use **View → Show Preparation Controls**
to hide or show it. Hiding it keeps the selected preparation active;
**Settings → Preparing for...** remains available.
The preparation state remains visible even when these controls, the general
status area, or the quick controls are hidden.
The preparation dialog's **Details** section links to the selected controller's evidence: the **APS
Disklavier Compatibility Table** for Disklavier and General choices, or the
**PianoDisc user manual** or **QRS user manual** for those manufacturers.

## Filenames and title formatting

Floppy and emulator preparation also stages unique DOS 8.3 names for songs whose
current filenames are incompatible. Existing valid names remain unchanged;
USB and app folder preparation keep descriptive filenames.
**Settings → Use DOS 8.3 Filenames** controls short filenames when the selected
destination does not require them.
Long MIDI filenames use the track number and song title and do not require converting
E-SEQ songs to MIDI. Disklavier screen formatting defaults on for Mark I,
Mark II (including XG), and Mark III profiles, and off for other destinations.
**Custom** keeps the current screen-formatting setting.
Change it under **Settings → Format for Disklavier screen**.
With screen formatting enabled, opening the title editor starts with the first
32 characters so an overlong title can be corrected. Cancelling leaves the title
unchanged. The **Long** column marks overlong titles without highlighting their
characters.

Standard MIDI export, E3 / DKC-800 / DKC-850, and ENSPIRE / DKC-900 preparation
automatically removes leading and trailing title spaces and collapses repeated
spaces. This applies to loaded songs and future imports, including titles embedded
during E-SEQ-to-MIDI conversion. It does not insert spaces between adjacent
letters, including at the old 16-character screen boundary: `abcdefghijklmnopQrst`
stays unchanged. The legacy split-title repair remains specific to Smart PianoSoft
song catalogs. Title cleanup is staged for review and undo; original files change
only when saved.

## Choose a delivery method

Applying a profile only stages changes. Choose a save or export command below
when you are ready to deliver the prepared songs.

| What you want to create | Where to go |
| --- | --- |
| A folder of songs for USB or an app | **File → Save As...** |
| One disk image for an emulator | **File → Save As Image...** |
| A numbered collection of disk images | **Utilities → Build Emulator Disk Set...** ([guide](emulator-disk-sets.md)) |
| A physical floppy from the current image | **Disk → Write Current Image to Floppy...** |

The controller determines song format and disk capacity. Emulator configuration
separately determines image type and numbering. Defaults feed **New Image**,
**Save As Image**, and **Build Emulator Disk Set**. Selecting an HFE delivery
starts those dialogs in HFE even when an open source image is IMG or a different
format was used previously. You can change the format in the export dialog.
An **Unsure** controller can still select a generic delivery format without
assuming a song format or disk capacity. Normal **Save** retains the original
image format; capture and recovery options retain their own settings.
The **Save Image and Apply to Floppy…** recovery action, offered after certain
floppy save or write failures, requires the prepared songs to fit on one floppy.
If they do not fit, the app stops before saving an image or selecting a drive.
Remove songs and try again, or use **File → Save As Image...** to create a split
set of disk images. For a numbered emulator disk set, use
**Utilities → Build Emulator Disk Set...**.
When the single prepared IMG is saved, the app offers drive selection, an
explicit overwrite confirmation, and a write with readback verification.
Cancelling or failing the image save does not write a floppy.

USB and PianoDisc app preparation use **File → Save As...** to
export a folder. For QRS USB profiles, copy the MIDI files to a USB drive and
select it on the controller. For Prodigy, import the exported MIDI files into
the PianoDisc iQ Player app and use its supported connection to the piano.

## What format conversion changes

Clavinova MDA songs are staged as Disklavier FIL through a MIDI intermediate;
the review identifies this container conversion. Files that cannot be converted
remain visible as unprepared, with an error, and their originals stay intact.

Review the staged result before saving. Fresh MIDI-to-Disklavier E-SEQ conversion
automatically generates Yamaha's channel-1 binary and channel-3 continuous pedal
layers; see the [conversion policy](mid2eseq-compatibility.md). Instrument merging,
the separate Pedal Compatibility utility, and broad metadata cleanup remain
separate choices.

ENSPIRE and other MIDI profiles automatically remove Yamaha startup volume
mutes when converting E-SEQ songs. See [startup-mute cleanup](preparation-reliability.md#automatic-yamaha-startup-mute-cleanup)
for the exact rule; later volume changes stay intact. For a manual conversion
that must retain muted opening notes, select **Preserve original volume
controls** in the E-SEQ-to-MIDI confirmation.

## Controller defaults

### Disklavier

Disklavier capabilities follow the [APS compatibility table](https://www.alexanderpeppe.com/disklavier-compatibility-table/),
reviewed September 7, 2026.

| Controller | Prepared format | Default delivery |
| --- | --- | --- |
| Mark I | E-SEQ | 720 KB floppy |
| Mark II | E-SEQ | 720 KB floppy |
| DSR1 upgrade, Mark II XG, Mark III | MIDI | 1.44 MB floppy |
| Mark IV / PRO | MIDI | USB folder export |
| E3 / DKC-800 / DKC-850 | MIDI | USB folder export |
| ENSPIRE / DKC-900 | MIDI | USB folder export |

These are preparation defaults. Mark II MIDI support varies by unit or firmware,
so its profile uses E-SEQ. ENSPIRE/DKC-900 requires converting E-SEQ to MIDI;
E3 and DKC-850 can play E-SEQ. Keep one song format per floppy.

### PianoDisc and QRS

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

## Emulator presets and USB sticks

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

## Accented and non-Latin titles

Edited MIDI titles accept printable Latin-1 characters, including accented names,
for Standard MIDI export, Mark IV, E3/DKC-850, ENSPIRE, Prodigy, and modern QRS
profiles. Early hardware profiles, E-SEQ titles, and Smart PianoSoft catalog
titles retain printable ASCII restrictions. Custom uses Latin-1 for MIDI when
Disklavier screen formatting is off; an unknown destination remains conservative.

Use **View → Title display encoding** to interpret existing titles as Latin-1
(the default), Windows-1252, Japanese Shift-JIS, Simplified Chinese GBK, or
Traditional Chinese Big5. This is an explicit display choice, without encoding
detection. Viewing a title or exporting it without an edit preserves its original
bytes. Accepting an unchanged decoded title in the editor also preserves it.
Edited titles use Latin-1 and the destination's restrictions; this choice does
not add Japanese or Chinese title writing. Automatic spacing cleanup leaves
non-ASCII titles alone when another display encoding is selected.

Next: [review and save changes](preparation-reliability.md), or
[build an emulator disk set](emulator-disk-sets.md).
