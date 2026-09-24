# Review, save, verify, and resume

## Prepare for a controller

**Preparing for...** combines a controller with a delivery method. Review its
proposed settings before applying. Mark I and uncertain Mark II controllers use
E-SEQ with a catalog and 720 KB disks. Applying a destination prepares the loaded
songs as staged changes and sets the corresponding editing mode. Imports follow
the selected destination. Review the musical report before saving, or use
**Edit → Undo** (**Ctrl+Z**) to reverse preparation. The destination row highlights
an active profile; its **Custom** button returns to manual preparation without
discarding changes already staged.

An E-SEQ destination disables conversion to MIDI; a MIDI destination disables
conversion to E-SEQ. Disabled controls explain the restriction. Switch
destinations or choose **Custom** to enable the other
conversion. This also applies to conversion after reading a floppy.

The [profile guide](preparation-profiles.md) records the compatibility sources
and the distinction between controller requirements and emulator configuration.
Profiles do not run instrument merging or the separate Pedal Compatibility and
metadata-cleanup utilities. Fresh MIDI-to-Disklavier E-SEQ conversion does
automatically generate Yamaha's binary channel-1 and continuous channel-3 pedal
layers. Required format conversion can remove unsupported metadata; the musical
report describes those changes.

## Keep unfinished edits

An ordinary in-place **Save** of an E-SEQ image updates the edited songs and
their active catalog while preserving unrelated payloads, including `NOTES.TXT`,
MIDI files, `PSONG.MNG`, `PDISK.MNG`, and the opposite E-SEQ variant and its
catalog. Only explicitly staged deletions remove unrelated files. Capacity
checks include those retained files. A populated catalog does not need refreshing
merely because unrelated files are present.

**Save As** / **Save As Image** still produce the clean delivery set for the
selected E-SEQ variant. Their output excludes unrelated payloads and regenerates
the matching catalog, while leaving the original image unchanged.

**Edit → Review Changes** shows original and proposed filenames, titles,
formats, playback order, and conversion reports. Select songs to discard their
edits. **Edit → Undo** (**Ctrl+Z**) reverses the latest staged action; **Edit →
Undo All** discards every staged change since the current files were loaded or
last saved. Undo and discard leave saved source files intact.

Enable **View → Show Save Destination** to display the save location above the
song list. This line is hidden by default; the app remembers your choice.

Folder Save records each completed file independently. Cancellation or a failed
write keeps all unfinished work and stops subsequent rename/catalog/metadata
steps. The result reports saved and remaining file counts. Use Save again to
retry remaining changes. Successful conversions already point to their saved
outputs, so retry does not collide with those outputs or repeat their writes.

Folder Save As asks once before replacing existing files, including files in
the current source list. The prompt includes existing catalogs, sidecars and
metadata summaries that will be replaced. All output is prepared before the
prompt, so declining leaves the files and pending edits intact. Confirmed
replacements honor the backup setting and restore earlier writes if a later
write fails. Songs with swapped output filenames are prepared from their
original inputs; two songs targeting the same output name still need distinct
filenames. If restoration fails, the error identifies retained recovery copies.

Backups are enabled for new settings; an existing preference is respected.
Title/order writes prepare and validate a sibling temporary before replacing the
destination. Combined E-SEQ title/order changes commit together. Catalog updates
also use checked temporary output.
Read-only destination files are preserved and reported as save failures. Use
Save As with a writable destination to export edits from a read-only source.

## Understand conversion reports

Reports compare notes, timing, channels, pedal events, titles, and metadata.
For Yamaha pedal conversion they independently check the expected binary
companions, continuous positions, event order, and timing, and explain generated
events and suppressed repeats. Reports retain these details when saved and
reloaded, in every supported language.
E-SEQ conversion preserves zero-volume CC7 by default; the conversion dialog
offers a playback fix when the potentially silent condition is found. Targeted
XF removal preserves unknown sequencer metadata and trailing bytes. Broad
cleanup is available as an explicit choice.

When merging MIDI channels, APS expands Reset All Controllers (CC121) into
explicit resets so one source channel does not reset other merged parts.
This includes Breath (CC2) and Foot Controller (CC4), restoring the surviving
source's value or 127 when no other source supplies a value. These defaults
follow the [Yamaha MOTIF-RACK ES implementation](https://jp.yamaha.com/files/download/other_assets/6/334856/motifrackes_en_om_b0.pdf)
and [MX data list](https://it.yamaha.com/files/download/other_assets/6/329536/mx49mx61_en_dl_a0.pdf).
Reset defaults and assignable-controller effects vary by device, so conversion
reports warn whenever CC121 is removed. Review playback on the intended player.

File Inspection provides one-click Type 0 conversion and piano channel merging
for the selected song. Type 0 conversion requires MIDI; piano merging also
supports native E-SEQ, retaining its container, timing, titles, order keys, and
Yamaha pedal-detail data. These stage real edits with the same Undo and Save
workflow as other utilities. Preview filters, temporary instrument choices, and
preview volume do not limit or alter the source used by these actions. The
preview reloads after an edit; returning to inspection refreshes saved or undone
changes. **Utilities → Merge Channels to Piano...** offers the same piano merge
for one song or all listed MIDI and E-SEQ songs.

Disk-set preview shows the actual prepared and packed songs, including musical
change reports and title provenance. Collection edits require an updated preview
before output can be built. See the [disk-set guide](emulator-disk-sets.md).

## Distinguish verification levels

Final image verification reopens IMG files or decodes final HFE files and checks
every contained file against the prepared output, including catalogs and song
order. Corrupted final output fails verification. **Disk → Verify floppy contents
after writing** adds a physical readback comparison. If readback is cancelled,
the result distinguishes completed writing from unverified contents.

Automated tests use self-created songs, temporary FAT12 images, fault injection,
and simulated devices. They validate preparation and delivery behavior. They do
not establish playback compatibility on an actual piano or test a physical USB
floppy drive; those require the controller, firmware, media, and drive combination.

## Preserve recovery and extraction work

Floppy discovery runs in separate helper processes with a ten-second wait and
Cancel. APS terminates unfinished helpers on timeout or cancellation while
keeping results from drives that responded. Reconnect an unresponsive drive
and retry: each attempt starts fresh, without requiring APS to restart.

After a failed or cancelled USB recovery read, **Save partial capture...** saves
the captured image and a JSON diagnostic record, including sector coverage and
affected files where the filesystem provides enough evidence. Unread regions
are identified; zero-filled bytes are not recovered song data.

Bulk extraction can save a local progress record. **Resume extraction job...**
restores its options, checks that input images are unchanged, and verifies output
hashes before skipping completed songs. Failed images are retried first. Modified
or unrelated destination files are preserved, and newly extracted copies get
unused filenames. Keep the original inputs, output folder, and job record together
until the extraction is complete.
