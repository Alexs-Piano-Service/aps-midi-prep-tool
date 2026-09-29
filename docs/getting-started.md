# Getting started

[All user guides](README.md) · [Project overview](../README.md)

APS MIDI Prep Tool opens songs or disk images, stages your edits, and writes
them when you save. For your first session, export to a new folder so you can
compare the result with the originals.

## Download and open the app

Choose a package for your operating system from the
[latest release](https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/releases/latest).
Use the app packages listed in that release, rather than GitHub's **Source code**
archives. Packaged releases do not require a separate Python installation.

| Package, when offered | How to open it |
| --- | --- |
| Windows standalone `APSMIDIPrepTool.exe` | Save the EXE in a folder on your computer or USB drive, then open it. |
| Windows installer (`Setup.exe`) | Run the installer, then open the installed app. |
| Windows portable ZIP | Extract the whole ZIP to a folder before opening the EXE inside. Keep its accompanying files together. |
| Linux AppImage | Save the AppImage, allow it to run as a program in your file manager's permissions, then open it. |

Menu names in these guides use English. Change the app's language under
**Settings → Language**. To see the introduction again, choose
**Help → Show Welcome Screen**.

## Open your songs

MIDI and Yamaha E-SEQ are song formats. IMG and HFE are disk-image formats,
which hold a disk's songs and catalog together.

| What you have | What to choose |
| --- | --- |
| A folder of MIDI or Yamaha E-SEQ songs | **Open MIDI Folder**. Despite its name, it also reads supported E-SEQ songs. |
| Individual songs | Drag the files into the main window. |
| A floppy image such as IMG or HFE | **Open Image**. A disk image is one file holding the contents of a floppy. |
| A physical floppy | **Disk → Read Floppy...**. For an irreplaceable disk, first make a copy with **Disk → Create Image from Floppy...**. |
| A ZIP containing songs | Drag the ZIP into the main window; APS extracts supported files, including songs in subfolders. |

Open one disk image at a time, separately from loose song files. A ZIP containing
multiple images, or an image alongside songs, is rejected without importing
anything. Extract it first, then open one image or select the songs separately.

ZIP extraction is temporary. Save wanted songs to a permanent folder before
clearing the list or closing the app. The original ZIP stays unchanged. APS
removes temporary contents once the list, undo history, and any image session no
longer need them. Retained ZIP contents share a 1 GiB / 10,000-file limit.

If a floppy drive is missing, connect it, insert a disk, and choose **Refresh**.
If the selected drive disappears, explicitly select a drive again before
continuing. See [floppy and image recovery](disk-recovery.md) for damaged media.

## Choose your piano and review edits

1. Choose **Preparing for...** above the song list, or **Settings → Preparing
   for...**. Select the controller and delivery method.
2. Review **Current → Proposed**, then choose **Apply and Prepare**. The app
   stages the required conversions and uses that destination for future imports.
3. Double-click a song's **Title** or **Filename** to edit it. Use the **Edit**
   menu for title cleanup, filename tools, and conversions.
4. Open **Edit → Review Changes...** to compare original and proposed titles,
   filenames, formats, and playback order. Review conversion details before
   saving.

If you do not know the controller, choose **I'm not sure** in the preparation
dialog. For manual preparation, the destination row's **Custom** button turns
off automatic preparation while keeping your preferences and staged changes.
See [Preparing for your piano](preparation-profiles.md) for controller choices,
short filenames, title limits, and disabled options.

**Edit → Undo** (**Ctrl+Z**) reverses the latest staged action. **Edit → Undo All**
discards staged changes since the current files were loaded or last saved.
These commands do not reverse changes already written to disk.

## Choose how to save

| Command | Result |
| --- | --- |
| **Save** | Applies pending edits to the current source where supported. Songs imported from a ZIP prompt for a destination folder. |
| **Save As** | Exports songs to a folder. Choose a new folder to keep the source files separate. |
| **File → Save As ZIP...** | Exports copies to a ZIP archive. |
| **Save As Image** | Creates an IMG/HFE disk image with the prepared songs. |
| **Disk → Save Files to Floppy...** | Saves listed files to a formatted physical floppy. |
| **Disk → Write Current Image to Floppy...** | Writes the loaded image or floppy session to a physical disk. |

If a **Save As** folder already contains output files, the app asks before
replacing them, including when you choose the source folder. Check that prompt
before confirming. **View → Show Save Destination** displays the current save
location above the list.

For an E-SEQ image, ordinary **Save** preserves unrelated files. **Save As** and
**Save As Image** produce a clean delivery set and regenerate its song catalog;
they are not complete archival copies of every file in the source image.

Main-list edits and conversions wait until you save. Standalone disk repair,
formatting, and writing act through their own dialogs; review the destination
there before starting. Boot-sector repair changes the selected image in place.

After saving, check the result and play it on the intended controller. A verified
file copy does not establish playback compatibility. If a folder save fails or
is cancelled, keep the session open and use **Save** again to retry unfinished
work. See [Review, save, verify, and resume](preparation-reliability.md).

## Customize the interface

- **Settings → Language** offers English, Spanish, French, German, Italian,
  Brazilian Portuguese, Bulgarian, Dutch, Polish, Japanese, Korean, and
  Simplified Chinese.
- **Settings → Font Size → Large** increases text and spacing by 20%.
- **Settings → Keyboard Shortcuts...** shows and changes shortcuts; menu labels
  omit the shortcut combinations.
- **View → Show Preparation Controls** hides or shows the destination row.
  Hiding it leaves preparation active. **Settings → Preparing for...** remains
  available.
- **View → Show Save Destination** shows the save location; it is hidden by
  default. View menu checkmarks indicate which areas are visible.

These preferences are remembered across launches. For presets that travel with
a USB copy, see [Startup configuration](startup-configuration.md).

## Get help or update

Use **Help → Check for Updates...** to check for a newer release. Supported
standalone Windows EXEs and Linux AppImages can **Update and restart** from the
notice. See [Self-updates and recovery](self-updates.md) for details.

Use **Help → Report a Bug...** for problems or **Help → Send Feedback...** for
suggestions. Include the action, the exact error, and the version shown under
**Help → About APS MIDI Prep Tool**.

[All user guides](README.md) · [Protect your originals](../README.md#protect-your-originals)
