# Changelog

All notable changes to APS MIDI Prep Tool will be recorded here.

This project follows a practical changelog format inspired by Keep a Changelog,
with release sections grouped by version and date.

## [0.8.9] - Unreleased

### Added

- Supplied website report receiver can create GitHub issue references alongside
  private bug report emails. It shares only a report ID and app version and
  requires server configuration. Failed emails can be retried; concurrent
  duplicate submissions are locked, and GitHub outages preserve email success.
- Developer playback tests render converted E-SEQ files with FluidSynth and
  check audible notes, pitches, rests, tempo changes, and volume-mute behavior.
  Linux CI requires these checks; they add no end-user application behavior.
- **Update and restart** downloads and verifies a published release, then
  updates the running standalone Windows EXE or Linux AppImage in place,
  including copies on USB drives. It resolves pending edits, defers during disk
  work, preserves adjacent music and configuration, and retains the
  previous application for recovery. Folder/source builds keep manual updates.
- **Settings → Font Size → Large** increases text and spacing by 20% over
  Regular, with the selection remembered across app restarts.
- **Disk → Repair Yamaha Boot Sector...** repairs IMG/IMA/BIN/VFD/HFE
  files in place, with backups only when checked. An optional target format
  (including HFE for Nalbantov) keeps the repaired original and creates a
  converted file beside it. Entire folders can be processed, optionally with
  subfolders, with cancellation and results per image. Supported 720 KB, 800 KB,
  and 1.44 MB FAT12 layouts preserve file contents, FAT differences, and unused
  space while repairing the boot sector and clearing hidden/system attributes
  so disk browsers can show the files. Other attributes are preserved. A missing
  first sector is prepended to raw images. Converted sector data is verified
  before saving; existing outputs and backups are never overwritten. This
  utility leaves pending song edits untouched and is translated in all 12
  interface languages.
- **Save Image and Apply to Floppy…** guides users from a repaired source boot
  sector or failed floppy save/write to a persistent IMG with pending changes,
  target selection, explicit whole-disk overwrite confirmation, and mandatory
  readback verification. Cancelling or failing image export does not start a
  write, and no separate blank-format step is needed. Multi-image exports retain
  all images and prompt for manual selection. Guidance is translated in all 12
  languages; a repaired boot sector is described as possible Yamaha protection.
- **View → Title display encoding** can display existing title bytes as Latin-1,
  Windows-1252, Shift-JIS, GBK, or Big5 without changing the source files.
- Dropping a ZIP extracts supported files from its folders and imports them
  individually. Save prompts for a destination for imported MIDI and E-SEQ songs,
  leaving the ZIP unchanged. Extraction progress and errors are translated in
  all 12 languages.

### Changed

- Successful self-updates retain one previous executable. Older recovery copies
  remain available until startup succeeds; failed updates retain their recovery
  directory. Existing backups from earlier versions are left untouched.
- Explicit piano merges show **Overlap repair: Smart** or **Overlap repair:
  Keep attacks** while the repair is staged, including remembered choices.
  Destination preparation leaves overlaps intact. Regression checks cover
  sustained notes beneath repeated strikes and E-SEQ startup-mute cleanup
  through interactive conversion, automatic preparation, disk import/export,
  and bulk extraction.

- Changing destinations replaces automatic preparation while preserving manual
  titles, filenames, ordering, and edits. Retained source bytes avoid MIDI/E-SEQ
  round trips. Undo restores the previous
  destination and its preparation, including across later imports.
- Both **Custom** entry points stop automatic preparation and keep all current
  staged work and preferences. **Edit → Reset preparation** separately removes
  automatic preparation changes, preserves manual edits and preferences, and
  supports Undo. Choosing a later destination still replaces the old preparation.
- **Preparing for...** starts with a concise delivery outcome and keeps the saved
  settings comparison and compatibility references under **Details**.
- Removed the extra song-readiness indicator beside the destination controls.
  Preparation requirements still apply when saving or exporting, and song counts
  and exceptions remain in the preparation dialog.
- **Review Changes** has a resizable split between the song list
  and a full original/proposed comparison. Summaries identify filename and title
  edits, and the first song's details appear immediately. Resizing no longer
  triggers automatic shrinking and recentering.
- Conversion dialogs, progress messages, and change reviews consistently use
  **E-SEQ**. E-SEQ-to-MIDI conversion automatically removes Yamaha startup
  volume mutes, including for ENSPIRE preparation, extraction, and MIDI copies.
  Later volume changes are preserved. Manual conversion offers a remembered
  **Preserve original volume controls** option for intentional muted openings;
  startup-mute correction remains the recommended default.
- User documentation now starts with a short workflow and a task-based guide
  index. Separate guides cover installation, saving, recovery, and audio, with
  clearer setup steps and troubleshooting navigation.
- Window menus group song edits and conversions under **Edit**, physical floppy
  and image-repair tools under **Disk**, and saved preferences under **Settings**.
  **File** exposes opening actions directly and adds **Quit**. Menu labels clarify
  floppy imaging, file saving, preparation controls, and dismissed messages;
  existing preferences and keyboard shortcuts are preserved. Menu labels omit
  shortcut combinations to keep menus compact; shortcuts remain configurable
  under **Settings → Keyboard Shortcuts...**.
- **Repair Yamaha Boot Sector...** and **Recover Damaged Image...** remove
  hidden/system flags automatically so Nalbantov/HxC Disk Browser can display
  files and folders. Boot-sector repair also updates already-valid images that
  still have these flags, while preserving file contents and other attributes.
- Boot-sector repair recognizes Yamaha 720 KB images whose file directory also
  needs recovery and explains that **Recover Damaged Image** is required,
  instead of reporting an unidentified layout. Raw images are checked before
  creating working copies; failed identification creates no repaired image,
  conversion, or backup.
- Bulk extraction removes its progress JSON after successful completion and
  removes the job lock when the operation ends. Failed or cancelled jobs retain
  their progress for resuming; completed jobs no longer leave a stale resume
  suggestion. Cleanup failures are reported without changing extracted files.
- **Save As Image** keeps the source filename without appending `_edited`,
  including floppy and Greaseweazle image exports.
- Piano channel merges prompt only when same-key notes overlap, with smart
  repair, attack-preserving trimming, and merge-only choices. The optional
  remembered choice requires checking a box and applies to future merges,
  including Type 0 piano remapping. **Settings → Overlapping Piano Notes...**
  lets users change the behavior or restore prompting at any time;
  **Show Dismissed Messages Again** restores the prompt. MIDI and native E-SEQ edits keep
  the existing Save and Undo workflow.
- Successful floppy-save recovery packages are limited to the five most recent
  saves and 30 days. Failed, cancelled, and unfinished packages remain until
  manually removed.
- Options controlled by **Preparing for...** show their effective values and
  are disabled, with one note per dialog and explanations in tooltips. This
  includes Read Floppy conversion and title cleanup, filename rules, and image
  song formats. **Custom** restores manual control.
- Standard MIDI export, E3 / DKC-800 / DKC-850, and ENSPIRE / DKC-900 preparation
  automatically stages title spacing cleanup for loaded songs and future imports,
  including E-SEQ-to-MIDI conversions.

### Fixed

- Ordinary USB floppy reads retain boot-probe, fast-read, and raw-fallback
  diagnostics in bug reports, including numeric Windows errors and read ranges.
- Fast floppy opening reads only sectors containing file bytes and active
  directory entries. Unreadable cluster slack or directory sectors after a
  verified end marker no longer prevent opening readable songs.
- Fast floppy opening follows folders and fragmented file chains without
  requiring an exact whole-disk image. Cycles and cross-links still stop opening.
- Exact Windows floppy imaging retries failed requests in smaller sector-aligned
  ranges. Unreadable sectors still prevent saving or replacing an exact image.
- MIDI Type 0 conversion rejects invalid channel and system-message data bytes
  before replacing an output file.
- E-SEQ-to-MIDI conversion preserves the order of tempo and meter changes at
  the same tick, including a return to an earlier value.
- Bulk Extraction keeps requested E-SEQ sources when MIDI conversion fails.
  Saved jobs retry the conversion and reuse unchanged retained sources.
- **Image Floppy** verifies HFE conversions against every captured sector
  before saving. A failed verification preserves the existing destination.
- Emulator disk-set rebuilds warn about untracked disks from older sets,
  identify their filenames, and keep those files for review.
- Damaged-image MIDI recovery keeps neighboring song tracks out of truncated
  songs and recovers each song separately.
- Failed exact floppy imaging offers a separate, explicitly labeled logical
  recovery image with diagnostics. Protected or unreadable sectors no longer
  leave disks whose songs are readable without an imaging recovery path.
  Reconstructed images are distinguished from exact sector captures.
- Normal FAT12 image loading checks redundant allocation tables, uses the sole
  usable copy when a mirror is damaged, and rejects conflicting viable copies
  with recovery guidance. Recovery considers viable FAT copies before carving.
- Initial FAT12 scans reject file and directory clusters outside the disk's
  data area, including out-of-range links later in a chain.
- Bug reports wrap the optional floppy-context heading at large font sizes.
  Bug-report and feedback form labels wrap within their shared column.
  Keyboard Shortcuts preserves room for command names when native fonts make
  translated categories or shortcut editors wider. Dialog regression tests use
  native button minimum sizes across platforms.
- Long translated buttons in Keyboard Shortcuts, Review Changes, and Overlapping
  Piano Notes stack when needed so narrow windows keep their chosen width.
  Bug-report questions wrap across the available width, and message boxes finish
  centering after Qt determines their initial size, preserving later user moves.
- Long translated overlap choices fit narrow windows with larger fonts, and
  preparation details finish rewrapping table rows after column widths change.
- Resizable forms open at compact default sizes. **Preparing for...** fits its
  collapsed summary and expands for **Details** until the window is manually
  resized; shared form windows no longer multiply their initial size by the font height.
  Bulk Extraction, Overlapping Piano Notes, Keyboard Shortcuts, and Review
  Changes use smaller starting sizes while preserving scrolling and resizing.
- **Save**, **Save As**, and **Save As Image** use their original heading fonts.
  The extra preparation status and save-button row has been removed.
- The Windows updater recognizes the published standalone filename
  `APS.MIDI.Prep.Tool.exe` as well as `APSMIDIPrepTool.exe`. Ambiguous downloads
  and installers remain rejected, and the existing release, checksum, and
  publisher-signature checks still apply. The filename failure and correction
  are verified in source tests; native Windows update acceptance remains pending.
- Linux packaging now verifies external-image-change protection in the completed
  AppImage before generating its release checksum. Earlier Linux packages can
  overwrite external changes to an open image; reopen after outside edits or
  repairs, or save pending edits to a new filename until using a verified build.
- **Save As Image** also refuses to overwrite a changed open source, including
  aliases, resized images, and split-output filenames. Saves recheck the source
  immediately before replacement after syncing the staged output.
- Resizable forms keep the user's chosen size and position instead of repeatedly
  fitting and recentering during resize. Bulk Extraction has a scrollable form
  with Extract and Cancel always visible, including on smaller screens.
- **Overlapping Piano Notes** keeps its controls together when enlarged and
  scrolls on smaller windows. Changing overlap behavior preserves the window's
  size and position, with Save / Merge Channels and Cancel kept visible.
- **Keyboard Shortcuts** keeps its table and buttons usable while resizing.
  A temporary shortcut editor no longer appears over the table header, and
  duplicate-shortcut warnings keep the editor open at the chosen size and position.
- **About**, **Disclaimer**, **Report a Bug**, **Send Feedback**, and
  **Preparing for...** fit the screen and scroll when needed, with action buttons
  kept visible. Preparation table rows rewrap as columns resize, and translated
  bug-report questions remain readable in narrow windows. Floppy options and
  Welcome page changes preserve the chosen window size; progress updates and
  message details no longer automatically recenter an open window.
- Image saves stage and sync completed output beside the destination before
  replacing it. Cross-filesystem copy failures leave existing images intact.
- E-SEQ conversion and channel merging respect valid declared stream lengths,
  ignore opaque binary trailers, and reject incomplete commands at the stream
  boundary. Conversion also rejects illegal MIDI channel data bytes before
  writing output. CC7 playback analysis now runs in linear time.
- FAT12 image loading rejects cyclic cluster chains and reused directory
  clusters with recovery guidance, including deeply nested directory cycles.
- DOS 8.3 renames reject existing case-equivalent target names on every host
  platform before moving any files.
- Piano overlap repair separates notes by MIDI port and preserves unrelated
  zero-duration notes and unmatched releases. Generated releases retain their
  destination, and resets and deferred releases cannot affect another port.
  Type 0 conversion restores port routing when combining source tracks.
- Main-window headings use the desktop UI font, with button text and spacing
  proportional to body text. This preserves the Linux layout proportions while
  avoiding oversized headings and excessive spacing with Windows' smaller
  default font. Font-size preferences and display scaling remain supported.
- Image exports preserve non-ASCII DOS filename identities, allowing renames,
  replacements, title edits, deletions, and regenerated song catalogs to find
  the correct file. Windows edits use temporary ASCII aliases so mtools cannot
  lose OEM characters during command-line conversion; saved images retain the
  original filename bytes. Yamaha images with these filenames no longer trigger
  unnecessary directory recovery
  or changes to original file sizes during boot repair.
- FAT lookups and regenerated song catalogs preserve exact DOS filename bytes;
  Unicode case expansion no longer merges distinct names such as STRAßE.FIL
  and STRASSE.FIL. Ambiguous file lookups are rejected.
  Image edits also reject case-colliding FAT entries before invoking mtools,
  preventing an operation on one song from affecting another.
- Repairing an open image resolves pending edits and reloads the editor after
  repair. Ordinary image saves reject source changes made since opening,
  preventing stale working copies from undoing repairs or external edits.
- Save As Image remembers manual format choices for each preparation profile
  and destination. Switching destinations restores that destination's default
  or its own remembered choice.
- Folder boot repair reports unreadable directories separately from image
  failures and identifies incomplete scans while repairing accessible files.
- **Save As Image** remembers the selected image format across dialog openings
  and app restarts. Cancelled dialogs and required IMG exports for floppy
  delivery leave that preference unchanged.
- **Review emulator disk set** keeps its size and position when resized,
  switching tabs, or editing album titles. Automatic content sizing no longer
  fights window resizing and causes redraw glitches.
- MDA-to-MIDI conversion now uses the source filename as well as the bytes to
  identify Clavinova containers, matching native channel merging. Affected
  variants retain their early notes, tempo, and meter in conversions, previews,
  and conversion reviews, including after staged piano merges.
- E-SEQ inspection and conversion now reject incomplete commands at the end
  of a file instead of silently producing a partial MIDI. Failed conversions
  create no output and leave existing destinations and staged songs unchanged.
- **Merge Channels to Piano** now works on E-SEQ songs in File Inspection and
  the batch utility, including folders, disk images, and pending additions.
  Edits preserve the native container, timing, titles, order keys, and Yamaha
  pedal detail, and use the existing Save and Undo workflow. Clavinova staged
  edits retain the song's logical name and catalog order. MDA variants recognized
  by filename merge from the correct event offset and remain recognizable after
  editing.
- Saved E-SEQ catalogs now follow the visible song order instead of stale
  filename-derived keys. Renaming and reordering an existing song in the same
  save also works, and Clavinova catalog ordering remains supported.
- MIDI discovery and conversion share validation for standard format numbers,
  declared track counts, timing divisions, and complete declared track chunks.
  Malformed files are rejected consistently; extended headers remain supported.
- Windows floppy-save failures now explain unsupported directory access
  separately from free-space query failures and point to saving an image before
  an explicitly selected whole-disk write. Recovery paths and partial-write
  warnings remain visible, with guidance in all 12 supported languages.
- Windows file saves check and journal file attributes, clear Read-only on all
  planned replacements and deletions before publication, and restore original
  attributes on replacements or during guarded metadata rollback.
- Modern preparation and **Trim Title Spaces** no longer insert a space at a
  lowercase-to-uppercase transition after character 16. Smart PianoSoft catalog
  split-title repair remains a separate operation.
- Failed or cancelled Windows file saves remove their staging files when the
  target identity and original contents can still be verified. Remaining
  temporary names appear in the recovery manifest and failure dialog. Saves
  also check FAT root-directory space when Windows permits metadata access.
- Linux floppy saves compare individual files with a cancellable read instead
  of rereading the entire raw disk for each file, avoiding stalls on unused
  sectors during **Checking existing**.
- The shorter **Save To Floppy** hint is translated in every supported language,
  fixing the catalog coverage failures in Windows and Linux CI.
- Windows **Save To Floppy** works with ordinary loaded files, including pending
  edits, conversions, filenames, and piano catalogs. It preserves unrelated
  files and asks before replacing matching filenames. Prepared files use native
  copying, recovery backups, staging-space checks, and content verification.
- Editing protected songs in an image clears their DOS protection flags only in
  the temporary output image, preventing invisible `mdel` confirmation prompts.
  Original images and unrelated files retain their attributes.
- ZIP imports release unused extraction folders after saving, clearing or
  replacing the list, and cancelled imports. Files needed by pending operations,
  image sessions, or Undo remain available. All retained ZIP extractions share
  a 1 GiB / 10,000-file limit, including companion files.
- Emulator image export uses a verified publication transaction and retains
  original images and a recovery manifest if rollback is incomplete.
- MIDI output selection follows the backend and port name across refreshes.
  Missing, duplicate, or changed outputs stop playback or select audio preview
  with an explanation instead of redirecting notes to another device.
- Saved emulator disk numbers survive restart. The preparation row's **Custom**
  button keeps current filename, title-formatting, and export preferences as well
  as staged changes.
- MIDI discovery accepts bounded extended headers and reports malformed or
  unreadable candidates rather than silently skipping them.
- Modern MIDI destinations allow printable Latin-1 title edits, including
  accented names. Legacy destinations keep conservative ASCII validation.
  Unedited titles retain their original bytes during folder export.
- Development metadata advances to 0.8.9 after the published 0.8.3 release.
  Release checks reject stale planned-changes wording in a released README.
- Windows file-level floppy saves retry an unsupported free-space query with
  the older Windows API. Initial directory and file-metadata errors stop saving
  before file changes. Bug reports include the save stage, API error codes,
  attempted target changes, and completed file counts.
- Windows file saves retain persistent recovery packages, check target contents
  against the opened session, and stage and verify replacements before publishing
  them. Insufficient staging space stops the save without changes; PIANODIR.FIL
  is published last. Final names and song bytes are verified.
- Windows raw writes forward cancellation to a bounded helper and wait for its
  exit before permitting another write. Device-not-ready flush failures now fail;
  unsupported flushing requires physical readback. USB formatting verifies the
  actual disk, and fallback capacity probes read aligned, complete sectors.
- Raw-write time limits remain enforced if cancellation diagnostics or final
  result writes stall. Recovery checksums remain compatible with Python 3.10.
- Bug reports identify the build commit and distinguish untouched disks from
  partial writes. A denied raw write no longer implicitly switches to file copying.
- Drops containing multiple disk images or an image alongside other files,
  including ZIP contents, show a warning before importing anything. Extract
  such ZIPs first, then drop one image or select the songs separately.
- ZIP extraction shows its progress dialog before reading the archive and
  advances by bytes while unpacking large files. Windows ZIP-drop regression
  tests compare paths independently of their separator spelling.

## [0.8.3] - 2026-09-18

### Added

- Floppy Read, Image, Format, Save, and Write dialogs offer **Refresh** to
  detect newly connected drives without reopening the dialog. Refresh keeps
  the selected drive when available and preserves the other dialog options.
- Pushes to the public repository's `main` branch automatically build a signed
  Windows EXE after Linux and Windows CI pass for the same commit. The EXE and
  Windows manual test kit are available as a workflow artifact.
- **Utilities → Back Up Mark IV Music...** copies a mounted or copied Mark IV
  library into named album folders with checksum verification and a manifest.
  The Details pane shows the selected album's tracks and applicable conversion
  notes; double-clicking an album opens its full details. Warnings and errors
  appear in a separate report. Optional
  E-SEQ-to-MIDI conversion creates verified MIDI files, with a checkbox to keep
  the originals alongside them. Cancellation and partial failures keep completed
  files, and existing backups can be verified again from the utility.

- Native Windows tests exercise real IMG/HFE round trips, clean versus ordinary
  E-SEQ saves, bundled mtools without PATH access, capacity failures, reserved
  filenames, locked destinations, and cancellation of helper descendants.
  CI publishes a portable clean-machine manual test kit with generated songs,
  mixed and damaged images, checksums, a PowerShell launcher, and result records.

- An optional `aps-midi-prep-tool.json` beside the executable presets all saved
  preferences, remembered dialogs, shortcuts, and paths at each launch. Instrument
  and delivery selections supply preparation defaults; relative paths support
  USB deployments on Windows and Linux, including AppImage releases. Open MIDI
  Folder and Open Image now remember their starting folders. A configuration
  guide and Nalbantov example cover deployment and supported setting names.
- A polite review invitation after the third successful physical floppy read,
  with Write a review, Remind me later, and Never ask again choices in all 12
  languages. Counts and choices persist; reminders wait three more successful
  reads. Image opens, failed reads, and cancelled reads do not advance the count.
- Linux and Windows CI on branch pushes and pull requests, with mtools installed
  for real-image regression tests. The tagging helper and signed packaging
  workflow require a recorded pass for both platforms on the exact commit.

### Changed

- Windows local and signed builds share one packaging script and require the
  complete verified Greaseweazle runtime. The final signed EXE is smoke-tested
  for its window, IMG/HFE workflows, and built-in piano MP3 export.
- Release tags and packaging validate application, changelog, README, and
  release metadata together. Packages are staged in drafts; the publication
  helper requires every declared asset and clean-machine evidence matching
  each package's SHA-256.
- The legacy bug-report token is identified as public compatibility data;
  security documentation describes the outstanding server-side audit.
- View visibility actions consistently say **Show**, with checked meaning
  visible. The **Preparing for...** row is shown on new installations.
- Preparation defaults select descriptive MIDI filenames unless the destination
  requires DOS 8.3 names. Long filenames also work without E-SEQ conversion.
  Disklavier screen formatting defaults on for Mark I, Mark II, and Mark III,
  and off for other profiles, including Custom.

### Fixed

- Mark IV translation tests preserve platform-specific path separators and
  exercise both Windows and POSIX paths on every platform.
- Translation coverage now includes recovery dialogs, Mark IV diagnostics,
  malformed MIDI/E-SEQ errors, batch results, and file-list tooltips in all
  12 languages. Switching languages refreshes existing controls and sorted
  rows without changing song metadata. Qt dialog controls follow the selected
  language and use consistent button labels.
- Floppy Refresh leaves a missing target unselected and preserves the chosen
  connection type. Select a drive explicitly before continuing, even when
  another drive is available or a disconnected drive reappears.
- Windows package smoke tests read UTF-8 reports explicitly, preserving accented
  paths when the system's default text encoding differs.
- Type 0 batch conversion, renaming, and GUI save backups share collision-safe
  name planning and exclusive creation. Selected recordings, intended output
  names, and existing backups are protected in either processing order.
- Mark IV drive discovery uses a disposable helper process with a ten-second
  deadline. Cancel and Close recover from stalled volume queries, and retries
  start fresh without terminating a backup-writing thread.
- Mark IV backup progress uses a durable per-file journal and periodic compact
  snapshots, avoiding quadratic manifest writes for large libraries. Verification
  replays interrupted progress, including MIDI-only original replacement.
- Channel collapse restores Breath (CC2) and Foot Controller (CC4) after CC121,
  using Yamaha synth defaults while retaining other source channels' settings.
  Conversion reports flag removed CC121 commands because device-specific reset
  effects cannot always be reproduced.
- Floppy discovery uses separate helper processes that are terminated on timeout
  or cancellation. Retrying starts fresh even if a previous driver call never
  returns, without restarting APS.
- Mark IV backup checks include space for optional MIDI conversions before any
  copying begins, and recheck space during conversion. This includes the period
  when originals and MIDI copies coexist even with original retention disabled.
- Windows helper cancellation and timeouts wait for descendant processes to
  finish terminating before cleaning up their files, avoiding intermittent
  sharing violations after stopping a process tree.
- Read Floppy and Bulk Extraction enable descriptive naming for Custom and
  other destinations that do not require 8.3 names, even when an older manual
  8.3 preference is saved. Existing installations receive the new naming
  default once; later unchecked choices remain saved. Unchecking naming still
  reads every file and preserves the original filenames.
- Overlong titles use the normal table font and are identified in the Long
  column. The title editor starts with 32 characters when Disklavier screen
  formatting is enabled, allowing an overlong title to be corrected.
- Audio previews reuse completed renders across Play clicks and inspection
  sessions, with cache invalidation for changed songs, preview settings,
  SoundFonts, and rendering tools.
- Floppy drive discovery runs in the background with cancellation and a bounded
  wait, so a stalled capacity, geometry, or volume-label query cannot freeze the
  interface before the drive chooser opens.
- SoundFont downloads supplement system certificate roots with bundled Mozilla
  roots while retaining HTTPS verification.
- Mark IV device-source resolution recognizes POSIX device paths on Windows;
  cross-platform tests compare resolved mount paths consistently.

- Disklavier normalization preserves same-tick pedal and program reversals,
  repeated settings after note activity or resets, and reset effects on routed
  legacy pedals. Combined piano merging normalizes the final channel sequence.
- MIDI channel merging and Type 0 piano remapping apply Reset All Controllers
  before collapsing source channels, preserving pedal, pitch-bend, and other
  controller resets while restoring settings still held by another source.
- In-place rename rollback preserves every original in filename chains and
  swaps, and retains recovery copies with a path manifest if restoration fails.
  Rename-helper backups use unique names and exclusive creation to protect
  selected sources, planned outputs, and existing backups from being overwritten.
- Emulator image set names sanitize Windows device names such as CON, AUX,
  COM1, and names with extensions such as CON.mid before creating output folders.
- Bulk extraction and emulator image sets share the Windows device-name check,
  including superscript COM¹–COM³ and LPT¹–LPT³ names with or without extensions.
- Windows CI and signed release builds locate mtools using the MSYS2 setup
  action's actual installation directory. The dependency check reports each
  command's path and version, and identifies missing or unusable executables.
- Linux CI installs the PulseAudio runtime required to import Qt Multimedia
  during test collection.
- Both CI platforms install a pinned Greaseweazle helper for HFE image tests.
  Windows tests compare the report's portable path separators and fully delete
  inspection dialogs between tests to avoid Qt shutdown crashes.
- MIDI channel merging and Type 0 piano remapping preserve note endings from
  All Notes Off and related mode commands, including notes spread across Type 1
  tracks and overlapping pitches, without stopping unrelated parts.
- All Sound Off (CC120) retains its immediate mute when no unrelated source
  part could still sound. Otherwise, channel collapse uses note releases and
  explicitly reports the lost immediate-mute behavior in Conversion details.
- Pedal cleanup retains repeated presses after Reset All Controllers and
  recognized GM, GM2, GS, XG, and System Reset messages.
- Windows image preparation now finds tools in nested bundled directories and
  uses that same lookup for extraction fallback. Windows build scripts bundle
  all required mtools commands instead of relying on the recipient's PATH.
- Image tools have a two-minute timeout and cannot wait for hidden console
  input. Cancellation stops the helper's Windows process tree; APS retains
  disk resources and shows a friendly explanation when closed during disk work.
- Ordinary Windows floppy reads now use cancellable I/O with a 30-second limit
  per read request. A stalled read stops with recovery guidance instead of
  retrying indefinitely or treating a timeout as missing data to fill with zeros.
- Image warnings use consistent displayed path separators, album folder notes
  preserve capitalization on Windows, and symlink tests skip when Windows does
  not grant the required privilege.
- Ordinary E-SEQ image Save preserves unrelated files and the opposite variant's
  catalog. Catalog regeneration and capacity accounting no longer assume that
  in-place saves should remove non-song data. Save As/export retains clean
  delivery output, and an unrelated MDA no longer switches an existing catalog's
  selected variant.
- Emulator song discovery accepts normal song extensions and extensionless files
  while ignoring backup/temp names and dot-prefixed copies and subfolders.
- Bulk extraction safely renames Windows device names with extensions, including
  `CON.txt`, `AUX.backup`, and `LPT1.foo`.

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
  A single Nalbantov option is available for floppy-capable Disklavier profiles,
  including Mark III, using HFE images numbered from `DSKA0000.HFE`.
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

- E-SEQ saves and disk exports omit MIDI management catalogs and other non-E-SEQ
  files while retaining their song and album titles. Song detection uses file
  contents, including extensionless E-SEQ files, rather than trusting `.FIL`.
- E-SEQ File Details omits converter provenance. The Type column shows format
  and arrangement, with write protection kept in File Details.
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
