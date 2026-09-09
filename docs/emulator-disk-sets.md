# Build a floppy-emulator disk set

Use **Utilities → Build Emulator Disk Set...** to turn MIDI or Yamaha E-SEQ
songs into numbered IMG or HFE disk images. The source songs are left unchanged.

## Create a set

1. Choose the folder containing your songs.
2. Choose **One album per folder** to keep albums separate, or **Fill disks
   automatically** to combine songs across folders.
3. Choose an output folder, MIDI or Yamaha E-SEQ contents, IMG or HFE format,
   and the disk capacity supported by your player and emulator.
4. Optionally enable **Include Song Lists** to create a text list for the whole
   set. Expand **Naming and capacity options** to change the filename prefix,
   starting disk number, or free-space reserve. For E-SEQ output, you can also
   set a shared album title.
5. Start preparation and review the proposed disks, songs, title sources, and
   musical changes. Exclude albums, change album order, or correct album/song
   titles as needed. Choose **Update Preview** after edits, then **Build Reviewed
   Output** to write the reviewed set. The app asks before replacing existing
   output files.

New disk sets start at `DSKA0000.hfe` by default. To append to an existing set,
choose its next unused disk number under **Naming and capacity options**.
Keep the setup files from an existing emulator USB stick and follow the emulator
manufacturer's instructions when preparing the stick. Test the images on your
player before relying on them.

Preview uses the actual conversion and packing results, including catalog space
and the free-space reserve. Editing the collection rebuilds the proposal; the
build button stays disabled until the updated proposal is shown. Every final
IMG/HFE is reopened and its song and catalog contents are checked against the
prepared output. This verifies delivered contents, not physical piano playback.

HFE exports also set the interface density to match the selected disk capacity.
For example, 720 KB images use double density and 1.44 MB images use high
density, following the [HxC HFE specification](https://hxc2001.com/floppy_drive_emulator/HFE-file-format.html).
This corrects earlier exports that marked every IBM-format HFE as high density;
the correction preserves the track data and bitrate. Re-export an older image
with the correct capacity to apply the updated header.
For 2.88 MB disks, use IMG: the Greaseweazle HFE writer rejects tracks of that
size, even though the HFE specification defines an extended-density mode.

## Choose how folders become disks

**One album per folder** starts a new disk for every folder containing songs.
An album that exceeds one disk's capacity continues on extra disks; different
folders never share a disk. Empty folders are skipped.

This layout always includes nested folders. Each folder's own songs form an
album, including songs directly in the selected folder. Folders and songs use
natural number order, so `Album 2` comes before `Album 10`. Shuffle randomizes
songs within each album.

For example, with a Nalbantov or Gotek/FlashFloppy preset:

```text
Music/
  Album 1/    → DSKA0000.hfe
  Album 2/    → DSKA0001.hfe
```

If Album 1 needs two disks, Album 2 starts on `DSKA0002.hfe` instead. Output
numbers follow the build order; they are not copied from source folder names.

**Fill disks automatically** combines the selected songs and fills each disk
before starting the next. Turn **Include nested folders** off to use only songs
directly in the selected folder. The app remembers this setting when you switch
layouts, along with your saved layout choice.

## Include a song list

**Include Song Lists** creates one combined text file in the output folder.
It lists every image, album, and track in playback order, using song titles
where available and filenames for untitled tracks.

The list covers the whole build, including nested albums and albums split across
disks. When several folders share an image, it identifies each track's source
album. Warnings about preserved damaged MIDI files appear in the list as well
as the completion dialog.

## Keep album and song titles

Leave existing `PDISK.MNG`, `PSONG.MNG`, and `INDEX.csv` files with the songs they
describe. The builder reads catalogs separately in each folder.

For **E-SEQ output**, the builder creates `PIANODIR.FIL` automatically. In
**One album per folder**, the album title comes from that folder's `PDISK.MNG`,
or from the folder name if no title is available. A shared album title entered
under **Naming and capacity options** overrides those album titles.

Song titles use the first available matching source:

1. The folder's `INDEX.csv`, then the collection's `INDEX.csv`.
2. The folder's `PSONG.MNG` song catalog.
3. The title embedded in the song file.
4. The filename, if no title is available.

Catalog matching supports original filenames, numbered long filenames from
extraction, older numeric extraction names such as `01 - 01.mid`, and identical
renamed copies in the same folder. Catalogs saved with either Windows (CRLF) or
Unix (LF) line endings are supported. Missing or invalid metadata falls back to
other available titles.

For **MIDI output**, images include valid `PSONG.MNG` and `PDISK.MNG` catalogs
when available in the source folders. This does not depend on **Include Song
Lists**. Catalogs follow each image's actual filenames and playback order,
including shuffled songs and albums split across disks. When automatic filling
combines albums, their song catalogs are combined and the compilation's disk
title uses its image number. The song list retains the source album titles.

## Understand damaged-file warnings

Some recovered MIDI files contain filler where disk sectors could not be read.
Other files have embedded titles that cannot be safely updated. For MIDI output,
the builder can preserve the original file bytes and, when a `PSONG.MNG` catalog
is available, store the title there.

Review the completion dialog and combined song list for the affected images and
files. Preserving these files does not repair missing music, and playback may
fail. Converting to E-SEQ still requires readable MIDI data.

[Back to the README](../README.md)
