# Changelog

All notable changes to APS MIDI Prep Tool will be recorded here.

This project follows a practical changelog format inspired by Keep a Changelog,
with release sections grouped by version and date.

## [0.8.2] - 2026-09-08

### Added

- E-SEQ File Details now reads the original header alongside the decoded MIDI
  preview: startup tempo, exact FB factors, meter, XG and piano-part flags,
  detailed pedals, note-channel mask, write protection, and counter display.
  Raw offsets and values remain visible; unproven flags and album-specific
  timing bytes are identified without guessing their meaning. All 12 languages
  include the new report.
- File Inspection has one-click **Convert to Type 0** and **Merge Channels to
  Piano** actions for the selected MIDI song. They refresh the preview, retain
  pending titles, and stage undoable edits for folder and image songs. Type 0
  keeps channels and instruments; piano merging sends all channels to MIDI
  channel 1 with Acoustic Grand Piano while retaining the MIDI type. Utilities
  exposes the same merge for one song or a batch under **Merge Channels to
  Piano...**. All new controls and status messages support all 12 languages.
- Controller categories for Disklavier, PianoDisc, and QRS, with manufacturer
  references and seven new PianoDisc/QRS preparation profiles. Model choices
  are indented in the open list while the selected value stays aligned.
- A **Preparing for...** workflow proposes controller and delivery defaults from
  the APS Disklavier compatibility table, with separate emulator configuration.
  Applying a destination stages its required format and mode; opposing
  conversions are disabled with an explanation. The destination row highlights
  active preparation and offers **Custom** to return to manual control.
  Nalbantov is available for floppy-capable profiles, including Mark III;
  the Nalbantov Slim model is limited to Mark II and Mark II XG.
- The **Edit** menu offers **Undo** (**Ctrl+Z**), **Undo All**, and **Review
  Changes**. Review compares original/proposed names, titles, and conversions
  and can discard selected songs' edits. Undo All clears staged changes since
  the current files were loaded or last saved.
- Conversion reports compare musical events, timing, channels, pedals, titles,
  and metadata. Welcome pages can launch disk capture, preparation, and title
  editing directly.
- Failed and cancelled USB-floppy recovery captures can be saved with sector
  coverage and diagnostics. Final emulator images are reopened and verified;
  physical writes offer optional content readback.
- Disk-set preview shows the actual prepared disks, albums, songs, title sources,
  musical changes, filenames, capacity, and warnings before writing. Album
  exclusions, order changes, and title corrections rebuild the preview.
- Bulk extraction can save a local job record and resume with failed items
  first. Input and output hashes are verified before reusing completed work;
  edited or unrelated output files are preserved.
- MIDI emulator images now include available `PSONG.MNG` and `PDISK.MNG`
  catalogs. Song records follow output filenames, title edits, shuffling and
  disk splits; disk-space and directory-slot reservations include the catalogs.
  This is independent of the combined song-list option.
- Emulator disk sets now read each folder's `PSONG.MNG` song titles and
  `PDISK.MNG` album title, including extraction-renamed songs, and include them
  in generated contents and the combined list. Explicit index edits and album
  overrides retain priority. Bulk extraction shares the PDISK album reader.
- Build Emulator Disk Set can create separate albums from every song folder,
  with folder-based E-SEQ album titles, per-folder song shuffling and INDEX.csv
  titles, and additional disks for albums that exceed capacity. Album contents
  never mix across folders, and optional song lists identify each source folder.
- Physical USB-floppy recovery now records sector-level read coverage, fallback
  results, unreadable and unresolved ranges, timing, trusted geometry evidence,
  FAT/root-directory checks, recognizable raw signatures, and an image SHA-256.
  Bug reports can include these diagnostics independently of console logs and
  offer optional disk, drive-compatibility, media-marking, and instrument context.

### Changed

- Backups default to enabled while preserving an existing user preference.
  Windows release builds run the automated suite before packaging and upload.
- E-SEQ conversions preserve zero-volume CC7 events by default and offer an
  explicit playback fix when detected. Targeted XF cleanup preserves unknown
  sequencer metadata and trailing bytes; broad removal is a separate choice.
- Shortened dialog instructions and reorganized the README around getting started,
  common tasks, and user needs. Detailed emulator disk-set guidance has its own guide.
- `Include Song Lists` is a build-wide control outside the individual disk
  settings. One combined overview covers all images, albums, and tracks,
  including nested albums and albums split across images. MIDI lists now name
  their albums, and automatic-fill lists retain each track's source album.
- The emulator-set dialog separates disk layout from recursive scanning and
  groups source and output controls, with expandable naming/capacity options
  and a live filename example. Automatic disk filling and all existing options
  remain available; previous scan preferences and packing behavior are retained.
- Folder-album layout now always includes nested folders. The scan checkbox
  stays checked and disabled in this layout; automatic-fill layout retains
  its own remembered scan preference.
- Direct USB-floppy recovery now uses smaller bulk reads with one sector-level
  fallback pass, a five-minute soft deadline, progress and poor-media cutoffs,
  and automatic clamping when a reliable smaller FAT12 geometry is detected.
  Failure messages distinguish low read coverage, geometry mismatch, genuinely
  blank media, and fully readable but unsupported data.

### Fixed

- Save As Image, New Image, and emulator disk sets consistently start with the
  selected delivery format, including HFE, even when the source is IMG or an
  older format preference was saved. An explicit delivery choice also works
  with an unsure controller; manual format changes remain available.
- PIANODIR status rows and filename-specific image-export progress messages
  now use the selected language in both folder and image sessions.
- Save As now asks once before overwriting existing destination files, including
  listed source files, instead of rejecting the folder. It prepares all songs,
  catalogs and optional metadata first, preserves the backup preference, and
  restores previous destination contents if publication fails. Renamed songs
  can safely exchange filenames; duplicate output names still require correction.
- Realtime SoundFont preview now finishes silent initialization before starting
  audible playback and the visual clock together. Temporary per-track metadata
  lets FluidSynth rewind accurately without losing sustained first notes;
  controller state is reset before replaying the requested start position.
  Startup no longer treats a delayed shell response as the beginning of music.
- File Inspection samples the current audio position when synchronizing its
  cursor, so queued position notifications cannot rewind the display. The
  playback timer also recovers without waiting for another notification, and
  inactive audio-player signals cannot reset live SoundFont or MIDI clocks.
  Piano-roll painting now selects visible note and pedal intervals instead of
  scanning the entire song every frame, retaining sustained events and drawing
  order while reducing display lag in dense files.
- File Inspection now keeps source order for simultaneous tempo events. An
  explicit initial tempo above 120 BPM is no longer overridden by the default,
  keeping displayed note timing and duration consistent with playback.
- Required MIDI Type 0 preparation now covers new imports, image additions
  and replacements, and emulator disk sets. Saving and export stop when a song
  cannot meet the selected profile. Floppy preparation retains DOS 8.3 names
  when naming files from their titles.
- E-SEQ conversion now follows recovered Yamaha Mark IV playback rules for
  zero/default tempo bytes and integer relative-tempo calculations. Slow MIDI
  input no longer writes a zero tempo byte that Yamaha would play at 117 BPM.
  Header time signatures and later meter changes are retained in MIDI exports.
- Fresh Yamaha preparation writes XG, detailed-pedal, and all 16 note-channel
  header flags from the actual output. Continuous-pedal routing now handles
  CC64/67 only, preserves sostenuto CC66, and avoids channel 3 when it contains
  notes. Imported archival flags and explicit MID2ESEQ preservation remain intact.
- HFE exports now set the interface density for the selected disk format,
  including double density for 720 KB and high density for 1.2/1.44 MB.
  Previously, every IBM-format HFE was marked high density. Track data and
  bitrate are preserved.
- Fresh MIDI-to-Disklavier E-SEQ conversions now follow MID2ESEQ's constant
  117 BPM clock, event scheduling, preparation delay, two-second trailer, and
  header/file layout. Both supplied Elton John reference conversions are
  reproduced byte for byte with pedal routing set to preserve. Returning
  recognized E-SEQ-derived MIDI preserves its existing timing, and Clavinova
  conversion retains its own container rules.
- Fresh MIDI-to-Disklavier conversion moves continuous channel-1 pedal lanes
  to channel 3, including their release values, and generates channel-1 binary
  companions using Yamaha's distinct sustain/soft thresholds. Repeated detail
  is suppressed; both layers share the original event's scheduled time, without
  changing note timing or duration. The pedal routine matches the original
  Mark IV helper across 75,536 independent executable checks.
  Binary-only pedals and occupied destination lanes keep their existing routing.
  Conversion reports explain generated companions and suppressed duplicates,
  independently verify expected event order and timing, and retain their original
  change flags. Saved reports retain pedal counts after being reloaded.
- MIDI-to-E-SEQ preservation keeps 4/4 until a later meter change actually
  occurs, honors the final source signature at a shared tick, and correctly
  reads tempo/meter events with multibyte MIDI length fields. Archival returns
  retain real meter changes during opening silence.
- MIDI conversion now stops each track at its end-of-track marker, retaining
  that track's ending silence and other tracks' later music. Trailing bytes
  after an ended track no longer become unintended notes.
- E-SEQ title and filename edits preserve unrelated header fields, including
  the legacy converter's timing metadata and exact file layout.
- E-SEQ output follows Yamaha/legacy seven-bit short-delay encoding, using the
  long-delay form for 128 ticks and above.
- MIDI-to-E-SEQ conversion now rejects unsupported F7 escapes before writing
  output, preventing later notes from being swallowed as SysEx data. Complete
  SysEx and continuation packets preserve their wire messages; the preservation
  timing policy also retains their existing packet times.
- E-SEQ delays inside SysEx now produce valid timed MIDI packets and advance
  subsequent events correctly. Converting back retains the embedded delays.
- Archival E-SEQ round trips keep tempo factors consistent with the restored
  header, including songs whose initial tempo command changes the header tempo.
- Yamaha directory reconstruction uses stream lengths and bounded FAT-chain
  data to recover complete songs when header sizes undercount or saturate.
- Cancelled or failed folder saves clear only completed changes and retain
  unfinished titles, order keys, conversions, and follow-up catalog work for
  retry. Cancellation stops later renames and metadata writes. Mixed title and
  conversion batches no longer lose ordinary title edits when completing.
- MIDI/E-SEQ title and order updates use checked sibling temporary files before
  replacement, preserving original data on failed writes or validation. Combined
  E-SEQ title/order changes are committed together, including Save As output.
  Read-only destinations remain protected; read-only sources can still be
  exported to a writable destination.
- Completed missing translations in welcome pages, conversion and disk dialogs,
  preparation profiles, change review, extraction-job validation, preview
  warnings, progress dialogs, and file pickers across all 12 supported languages.
  Preparation dialogs honor the saved language even without a main-window
  parent. Corrected mistranslations, clarified deletion warnings, and preserved
  specific action button labels when translating dialogs.
- Extraction and batch disk creation now read MNG catalogs whose CRLF endings
  were converted to LF. Album/song fields retain their correct offsets, and
  generated catalogs use CRLF. Batch creation also recognizes old numeric
  extraction names and identical renamed copies within the same folder.
- Catalog-backed MIDI disk sets no longer abort when a damaged song prevents
  an embedded-title update. Original MIDI bytes are preserved and titles stay
  in `PSONG.MNG`. Known bad-sector filler is reported even when title editing
  could succeed; completion warnings and song lists name affected images and
  files. Missing musical data is not repaired or converted to E-SEQ.
- The emulator-set dialog now centers only when opened and keeps its geometry
  when changing layout modes or expanding options. A shorter form and reserved
  description space avoid resize/recenter feedback and keep controls stable;
  the action button also retains its `Build Disk Set` label.
- PianoDisc System 3 IMG/HFE images now accept production disks that omit the
  optional version text, decode multi-line catalog titles correctly, and keep
  later valid songs when an individual catalog record is damaged.

## [0.8.1] - 2026-08-23

### Added

- A staged `Utilities > Merge Instruments to Channel 0...` workflow routes all
  channel-voice events in one selected MIDI song or every listed MIDI song to
  zero-based channel 0, replaces conflicting program and bank changes with
  Acoustic Grand Piano, and removes channel-mode commands. It works in folder,
  image, and floppy sessions while preserving the source SMF type, track
  structure, timing, metadata, SysEx, and pending title edits.
- A staged `Utilities > Strip XF Data...` workflow removes Yamaha XF
  sequencer-specific metadata and appended chunks from one selected MIDI song
  or every listed MIDI song, including songs in image and floppy sessions,
  while preserving musical events, tempo, track structure, SysEx, and
  continuous pedal data.
- Build Emulator Disk Set now offers a remembered `Include Song Lists` option
  that writes one UTF-8 overview file naming every resulting image and listing
  its songs in packed playback order, with per-image E-SEQ album and catalog
  metadata when applicable.
- Build Emulator Disk Set now writes Nalbantov-ready sequential slot names such
  as `DSKA0001.hfe`, with a configurable one-to-four-character prefix and
  starting number. HFE is the default output, and a configurable 32 KiB
  free-space safety margin is enforced on every packed FAT12 disk while the
  existing E-SEQ conversion, directory-file, and 60-song rules remain in force.
  Batch E-SEQ disks derive unique internal catalog IDs such as `DSKA-0001`
  directly from their slots instead of asking for one repeated catalog number;
  album titles default to the same per-disk IDs with an optional shared-title
  override. An optional remembered shuffle setting randomizes the discovered
  song order before conversion, naming, disk packing, and E-SEQ directory
  generation.

### Changed

- `Recover Damaged Image...` now appears under `File > Open`, alongside the
  other commands that open existing files, instead of under the physical-disk
  commands in `Disk`.
- Build Emulator Disk Set now presents its remembered subfolder scan as
  `Explore source folders recursively` and explains that clearing it limits
  discovery to songs directly in the selected source folder.
- Build Emulator Disk Set now uses nonblank titles from a source-folder
  `INDEX.csv` when rows can be matched by path, unambiguous filename, or
  SHA-256. Full titles are embedded in MIDI output, while E-SEQ output applies
  its 32-byte title limit; generated song lists reflect the title actually
  stored in each output file.
- Build Emulator Disk Set now asks before replacing exact image or song-list
  output collisions, with the existing files restored if the replacement
  commit fails. Its progress window also keeps a stable width and elides long
  paths in the middle while retaining the full text in a tooltip.

### Fixed

- Every literal UI string routed through the translation layer now has catalog
  coverage in all 12 supported languages, including File Inspection/audio
  rendering, pedal and XF workflows, mode actions, image recovery guidance,
  and the emulator-set onboarding title. Bulgarian now uses explicit
  translations instead of silently backfilling missing entries from English,
  and release checks scan translation call sites for future omissions.
- Normal image opening now recognizes protected FAT12 disks whose signed boot
  sector or unsigned geometry-only BPB stub is stored over the second FAT. The
  editable working copy restores or synthesizes a signed boot sector and mirrors
  the intact first FAT, preserving all original files without requiring Image
  Recovery.

## [0.8.0] - 2026-08-15

### Added

- Pedal Compatibility can now soften binary on/off CC64 sustain into smooth
  S-curve motion. Quick, Natural, Slow, and independent custom down/release
  timings are available for one chosen song or every listed MIDI song as a
  batch. The transform preserves each original 64/63 threshold tick, skips
  continuous and static pedal streams, supports SMF Types 0, 1, and 2 with
  PPQN or SMPTE timing, and remains staged until the user saves.

### Changed

- Bulk Extraction now has an independent, remembered track-number/title naming
  choice for MIDI already stored in an image as well as MIDI created from
  E-SEQ. Yamaha Smart PianoSoft `PSONG.MNG` catalog titles and track order now
  take precedence both in Bulk Extraction and when images or floppies are opened
  normally. Editing one of these catalog-backed titles updates `PSONG.MNG`
  instead of the MIDI track-name event. Optional E-SEQ conversion also offers
  cleaned embedded title spacing, enabled by default in this workflow.
- File Inspection now identifies CC64 sustain as binary, mixed, continuous,
  static, or not detected, using the same classification as Pedal Softening.
- Pedal Compatibility's individual-song target list now shows song labels
  directly without a repeated `One song:` prefix.
- The legacy Disklavier pedal option is now labeled explicitly as a channel-3
  to channel-1 remap, with its note and existing-pedal safety conditions stated
  in the tooltip.

### Fixed

- Tooltip-delay styling now filters Qt internal layout items before forwarding
  style hints to PySide, preventing `QProxyStyle.styleHint` type errors.
- Post-import file-list column fitting now expands the Title column to consume
  unused viewport width instead of leaving a blank area after the Type column.

## [0.6.13] - 2026-08-12

### Added

- `Utilities > Build Emulator Disk Set...` prepares an arbitrary folder of MIDI
  and Yamaha E-SEQ songs as a numbered series of raw IMG or HFE disks. A
  mutually exclusive disk-content choice produces either E-SEQ-only disks with
  unique DOS 8.3 `.FIL` names and a per-disk metadata-aware `PIANODIR.FIL`, or
  MIDI-only disks with `.MID` songs and no Yamaha directory file. Files are
  converted only when needed, E-SEQ output spills at the 60-song or FAT12
  capacity limit, every image is verified before committing, all choices are
  remembered, and existing images are never overwritten. The complete workflow
  and progress text are available in all 12 supported languages.
- Read-only PianoDisc System 3 floppy-image support for raw IMG/BIN-style
  sector images and HFE images. The proprietary catalog and compact performance
  stream are decoded directly to piano SMF0 files with 480 PPQ timing, sustain
  and soft-pedal events, DOS 8.3 filenames by default, an optional remembered
  choice for track-number/title names, and per-song damage reporting. Opening
  one of these images loads the decoded MIDI songs without modifying the
  source; Bulk Extraction can process the format as well.
- An SMF1-to-SMF0 conversion option that combines channel voice events on MIDI
  channel 1 (zero-based channel 0), removes bank and channel-mode events that
  can conflict after channels are merged, and selects Acoustic Grand Piano.
- Optional descriptive MIDI filenames for E-SEQ conversion, using track order
  and song title (for example, `01 - Moon River.mid`). The option is available
  during regular conversion and Read Floppy, and `Utilities > Name MIDI Files
  from Song Titles` provides the same naming as a standalone tool. In
  floppy/image mode, descriptive names are queued inside the image; choosing
  them as the conversion default disables the DOS 8.3 filename preference.

### Changed

- Bulk Extraction guidance in the Welcome dialog is now a dedicated workflow
  instead of an item in the Overview page.
- Filename, title, and type columns are compactly fitted to their current
  contents after file loads, conversions, batch renaming, title cleanup, and
  multi-file drops, without continuously resizing while the user edits.
- E-SEQ-to-MIDI conversion now offers a remembered option to remove leading,
  trailing, and repeated spaces from song titles before embedding them in MIDI
  files or using them in descriptive filenames.
- Long filenames are now allowed by default in folders, images, and floppy
  sessions. A remembered `Use 8.3 filenames` checkbox in rename dialogs and
  Settings enables legacy filename restrictions explicitly without taking
  space in the quick panel. The batch DOS 8.3 utility is available in every
  editing mode and queues image/floppy renames until save.
- `Use 8.3 filenames` and the conversion option to name MIDI files by track
  number and title now behave as inverse remembered defaults. Enabling either
  preference disables the other.
- E-SEQ filenames now unconditionally use DOS 8.3 names across conversion,
  image/floppy additions, manual renames, folder and image exports, and final
  save validation, regardless of the general filename preference.
- File Inspection collapses leading, trailing, and repeated spaces in titles in
  its left-hand file list for readability without changing title metadata.
- Completed the remaining common-interface translations for feedback, logging,
  disk and recovery status, SoundFont management, and audio rendering in all 12
  supported languages. Release checks now verify catalog coverage, placeholder
  integrity, and version consistency across the app, README, and changelog.

## [0.6.12] - 2026-08-04

### Added

- `Utilities > Bulk Extraction...` for extracting every file from every
  supported floppy image directly inside a selected folder. Per-image output
  folders can use image filenames or available `PIANODIR.FIL` album titles,
  with optional lossless E-SEQ-to-MIDI conversion during extraction. Conversion
  mode outputs MIDI in place of the original E-SEQ and omits `PIANODIR.FIL` and
  `MUSIC.DIR` by default; a separate default-off option can retain those source
  files alongside the MIDI conversions. A stable two-level progress window
  tracks both the full image set and files within the current image. Its
  controls, validation, progress, completion, failure, and cancellation text
  are included in all 12 supported languages.
- A default-off `File > Save Options > Create Album Subfolder for Save As
  Image` option that puts Save As Image output into the same catalog-number and
  album-title subfolder used by Save As folder exports. It covers image repacks,
  multi-image spill output, and Greaseweazle image preservation, with interface
  and completion text in all 12 supported languages.

### Fixed

- Direct USB floppy formatting now blocks raw images larger than the capacity
  reported by the selected drive, preventing attempts such as writing an IBM
  2.88M ED image to a 1.44M drive.
- Windows raw floppy short-write errors now report the image bytes completed,
  the requested and written chunk sizes, and likely disk-format or drive-capacity
  mismatches even when Windows supplies no error code.

## [0.6.11] - 2026-07-07

### Added

- Second-tier Yamaha PSR-600 `.BLK` Page Memory recognition for floppy images,
  physical disks, folders, file-open, and drag-and-drop workflows, with
  best-effort conversion of each BLK to one Type 1 MIDI whose recorded Melody
  banks are separate tracks. Melody setups containing a second voice descriptor
  with the recurring `0x7F` flag also produce clearly questioned layer tracks
  for auditioning, with both raw descriptors preserved in MIDI metadata because
  the flag's meaning is not yet confirmed. Accompaniment, Conductor, Multi Pad,
  style, and other proprietary data remain preserved in the source `.BLK`
  files.
- Space-bar playback toggling in File Inspection, covering both rendered audio
  previews and direct MIDI output.
- Per-channel General MIDI instrument selectors in File Inspection, with the
  recorded/default instrument shown inline, grouped searchable choices, and
  live preview-only overrides for realtime SoundFont or direct MIDI output.
- A 5%-400% live preview-tempo control in File Inspection that preserves
  recorded tempo changes while keeping audio, direct MIDI, seeking, duration,
  and the piano-roll playhead synchronized.
- A `Render Song...` menu in File Inspection that exports the selected song or
  all loaded songs to WAV or MP3 with the current channel selection, channel
  levels, instrument overrides, tempo, SoundFont, and preview volume.

### Changed

- File Inspection now groups related playback controls, reflows short channel
  lists to avoid empty columns, and uses shorter contextual tooltips.
- `File > Save As Image...` now asks for image type and disk size even when
  already editing an image, and can repack the current image contents into the
  newly selected disk size when needed.
- The Read Floppy option `Convert E-SEQ files to MIDI after reading` now starts
  unchecked for new users while still remembering the user's last accepted
  choice.

### Fixed

- File Inspection channel checkboxes now mute and restore channels in place
  during realtime SoundFont or direct MIDI playback, retaining the current
  song position instead of stopping, rebuilding, and returning to the start.
- File Inspection now marks channel controls with the same distinct 16-color
  palette used by piano-roll notes, so the channel list also serves as a
  legend.
- Realtime SoundFont preview now rebases its temporary MIDI tempo so the full
  5%-400% range stays inside FluidSynth's safe live-multiplier range. A tempo
  selected before pressing Play is also sent as the first live command,
  ensuring it takes effect without a restart.
- File Inspection SoundFont playback now keeps a realtime FluidSynth process
  active for tempo, instrument, and volume changes instead of replacing a
  rendered WAV. This prevents delayed jumps to 0:00 and avoids the pops and
  jitter caused by background rendering and playback-rate source swaps.
- File Inspection now initializes live program tracking before opening, and
  configures FluidSynth before its first note instead of stopping and
  restarting it after launch, preventing the dialog crash and silent previews.
- `File > Save As Image...` no longer skips the image-format and disk-size
  prompt when used again while already in Image Mode.
- HFE exports for IBM/Yamaha disk formats now normalize the HFE header metadata
  used by Nalbantov/HxC-style emulators, avoiding `0xFF` unknown
  encoding/interface values that could make some Disklaviers report
  "Unformatted Disk" even when the virtual disk contained files.

## [0.6.10] - 2026-06-11

### Added

- `File > Save As ZIP...` for exporting the current listed files as a single ZIP archive while leaving originals untouched.
- Help menu feedback submission through the same signed support-report channel, using the `feedback.php` endpoint.
- Settings menu font-size choices for regular, small, and compact UI text.
- `Utilities > Apply Pedal Compatibility...` as a standalone MIDI utility for optional pedal compatibility transforms outside the E-SEQ conversion workflow.

### Changed

- About, welcome, Help disclaimer, and project documentation now include clearer
  lawful-use, non-affiliation, and third-party trademark notices.
- E-SEQ to MIDI exports include the short APS conversion text marker, but omit APS archival round-trip header metadata by default while still accepting older metadata when present.
- E-SEQ to MIDI and SMF1 to SMF0 conversions now preserve pedal channels by default. `Utilities > Apply Pedal Compatibility...` can stage conservative legacy Disklavier channel-3-to-channel-1 pedal remapping, binary pedal values, duplicate/stuck pedal cleanup, or Piano Roll Vector note-18 sustain markers for listed MIDI files.
- Pedal compatibility options now start unchecked every time so preservation remains the default behavior unless a user explicitly stages a transform.
- Portuguese locale aliases such as `pt-PT` now use the included Brazilian Portuguese translation set instead of falling back to English.

### Removed

- Removed the removable USB-stick formatting utility. USB floppy emulator media preparation is vendor- and firmware-specific, and APS MIDI Prep Tool no longer tries to choose or write a USB-stick layout.

### Fixed

- Catalog numbers can now fall back to catalog-shaped HFE filenames when `PIANODIR.FIL` contains only album-title metadata.
- The immediate post-read Greaseweazle image-save flow now fills a blank Catalog Number field from the saved HFE filename.
- Linux builds now prefer the PNG application icon, avoiding a brief low-quality icon flash during startup.
- Drag-and-drop import highlighting now fills the entire file list, and font-size changes also scale the main window spacing, margins, row heights, and fixed controls.
- Save As ZIP dialog, progress, success, and failure text now has shared translation coverage for the supported UI languages.
- Image creation, image edits, floppy writes, and ZIP/Image temporary outputs now use ASCII-safe transient filenames so mtools and conversion tools can handle source or destination names containing accents, emoji, CJK text, and shell-sensitive punctuation.
- Missing source-file errors now show a localized, user-friendly message that asks users to confirm files still exist, external drives or cloud-synced folders are available, and moved or renamed sources have been reopened.

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
- `Report A Bug...` button on unexpected operation-failure dialogs, prefilled with the error message and recent logs enabled.
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
