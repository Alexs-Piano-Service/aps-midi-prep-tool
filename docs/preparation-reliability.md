# Review, save, verify, and resume

[All user guides](README.md) · [Getting started](getting-started.md)

Use this guide after preparing or editing songs. It explains how to review
pending changes, choose a save action, and recover from an interrupted operation.

<a id="prepare-for-a-controller"></a>

For your piano's song format and delivery settings, start with
[Preparing for your piano](preparation-profiles.md).

## Review or undo changes before saving

1. Open **Edit → Review Changes...**. The list summarizes each song's changes.
   Select a song to compare its original and proposed filename, title, and
   format, with playback-order changes and conversion reports below. Resize the
   window or drag the divider between the list and details for more room.
2. If needed, select songs in that dialog to discard their edits. **Edit → Undo**
   (**Ctrl+Z**) reverses the latest staged action; **Edit → Undo All** discards
   every staged change since the current files were loaded or last saved.
3. Choose the [save action](#choose-where-to-save) for your destination.

Staged changes are pending edits in the app. Reviewing, undoing, and discarding
them leave saved source files intact. Applying a preparation profile also stages
its changes; undoing that action restores the previous profile and staged state.

Enable **View → Show Save Destination** to display the save location above the
song list. This line is hidden by default; the app remembers your choice.

<a id="keep-unfinished-edits"></a>

## Choose where to save

| Action | Result |
| --- | --- |
| **File → Save** | Writes pending edits to the current source where supported. Songs imported from a ZIP prompt for a destination folder. |
| **File → Save As...** | Exports songs to a folder you choose. |
| **File → Save As Image...** | Exports songs as a disk image you choose. |

See the [getting-started guide](getting-started.md#choose-how-to-save) for ZIP export
and physical floppy options.

For E-SEQ images, these actions also differ in which files they retain:

An ordinary in-place **Save** of an E-SEQ image updates the edited songs and
their active catalog while preserving unrelated payloads, including `NOTES.TXT`,
MIDI files, `PSONG.MNG`, `PDISK.MNG`, and the opposite E-SEQ variant and its
catalog. Only explicitly staged deletions remove unrelated files. Capacity
checks include those retained files. A populated catalog does not need refreshing
merely because unrelated files are present.

**Save As** / **Save As Image** still produce the clean delivery set for the
selected E-SEQ variant. Their output excludes unrelated payloads and regenerates
the matching catalog, while leaving the original image unchanged.

### Existing files and backups

Folder Save As asks once before replacing existing files, including files in
the current source list. The prompt includes existing catalogs, sidecars and
metadata summaries that will be replaced. All output is prepared before the
prompt, so declining leaves the files and pending edits intact. Confirmed
replacements honor the backup setting and restore earlier writes if a later
write fails. Songs with swapped output filenames are prepared from their
original inputs; two songs targeting the same output name still need distinct
filenames. If restoration fails, the error identifies retained recovery copies.

**Settings → Save Options → Back Up Before Saving** is enabled for new settings;
an existing preference is respected.
Title/order writes prepare and validate a sibling temporary before replacing the
destination. Combined E-SEQ title/order changes commit together. Catalog updates
also use checked temporary output.
Read-only destination files are preserved and reported as save failures. Use
Save As with a writable destination to export edits from a read-only source.

### If a folder save stops partway through

Folder Save records each completed file independently. Cancellation or a failed
write keeps unfinished edits in the current session and stops subsequent
rename/catalog/metadata steps. The result reports saved and remaining file
counts. Keep the session open and use **Save** again to retry remaining changes.
Successful conversions already point to their saved outputs, so retry does not
collide with those outputs or repeat their writes.

## Understand conversion reports

Reports compare notes, timing, channels, pedal events, titles, and metadata.
For Yamaha pedal conversion they independently check the expected binary
companions, continuous positions, event order, and timing, and explain generated
events and suppressed repeats. Reports retain these details when saved and
reloaded, in every supported language.
Targeted XF removal preserves unknown sequencer metadata and trailing bytes.
Broad cleanup is available as an explicit choice.

### Automatic Yamaha startup-mute cleanup

When E-SEQ songs are converted to MIDI, APS automatically removes Yamaha
startup channel-volume commands that would otherwise carry a mute into MIDI
playback. This includes **ENSPIRE and other MIDI preparation profiles**, floppy
extraction, emulator MIDI output, and MIDI copies made during Mark IV backup.
Manual E-SEQ-to-MIDI conversion offers **Preserve original volume controls**.
Leave it unchecked for the recommended startup-mute correction. Check it when
the recording intentionally begins with muted notes or when preserving the
original volume automation matters. The choice is remembered, including when
the conversion confirmation is hidden. Automatic preparation and batch tools
continue to use the recommended correction.

Cleanup removes `CC7=0` commands at or before each part's first sounding note,
including setup after a long opening pause. This is a heuristic: it cannot
distinguish Yamaha initialization from an intentional mute over opening notes.
The preservation option keeps those commands and the later volume raise.
Channels are evaluated independently; channels
without sounding notes keep their commands. Later volume changes, including
intentional mutes and restored volume, remain intact. Native MIDI preparation
does not apply this E-SEQ cleanup. The cleanup does not replace the commands
with volume 100 or alter note strength, timing, or
pedals. The conversion report shows the before/after volume-command counts.
Original E-SEQ files remain unchanged until you explicitly save over them;
export copies to keep the originals.

Clavinova MDA preparation for Disklavier E-SEQ preserves volume commands through
its intermediate conversion. A subsequent MIDI export performs the same startup
cleanup as converting the MDA directly to MIDI.

Preparation profiles do not run instrument merging or the separate Pedal
Compatibility and metadata-cleanup utilities. Fresh MIDI-to-Disklavier E-SEQ
conversion does automatically generate Yamaha's binary channel-1 and continuous
channel-3 pedal layers. Required format conversion can remove unsupported
metadata; the report describes those changes.

### Optional channel merging and overlap repair

When merging MIDI channels, APS expands Reset All Controllers (CC121) into
explicit resets so one source channel does not reset other merged parts.
This includes Breath (CC2) and Foot Controller (CC4), restoring the surviving
source's value or 127 when no other source supplies a value. These defaults
follow the [Yamaha MOTIF-RACK ES implementation](https://jp.yamaha.com/files/download/other_assets/6/334856/motifrackes_en_om_b0.pdf)
and [MX data list](https://it.yamaha.com/files/download/other_assets/6/329536/mx49mx61_en_dl_a0.pdf).
Reset defaults and assignable-controller effects vary by device, so conversion
reports warn whenever CC121 is removed. Review playback on the intended player.

**Utilities → File Inspection...** (**F4**) provides Type 0 conversion and piano
channel merging for the selected song. Type 0 conversion requires MIDI; piano
merging also supports native E-SEQ, retaining its container, timing, titles, order keys, and
Yamaha pedal-detail data. These stage real edits with the same Undo and Save
workflow as other utilities. Preview filters, temporary instrument choices, and
preview volume do not limit or alter the source used by these actions. The
preview reloads after an edit; returning to inspection refreshes saved or undone
changes. **Edit → Merge Channels to Piano...** offers the same piano merge
for one song or all listed MIDI and E-SEQ songs.

Same-key overlaps prompt for smart repair, trimming, or the existing merge
behavior. Smart repair follows the piano merger's containment rule: remove a
long note only when it contains at least two surviving notes at distinct starts,
each no more than half its duration. It can remove the long note's leading and
trailing portions, including a legitimate sustained note beneath repeated
attacks. Destination preparation never applies overlap repair, even when Smart
is remembered for explicit channel merges. Repair pairs notes before channel
routing, combines unisons, and releases trimmed notes at the next attack's exact
tick (zero release gap).
Native E-SEQ delay commands remain intact. Independent Type 2 sequences never
count as overlaps with one another. A checked future-merges preference persists
across restarts; **Settings → Show Dismissed Messages Again** makes affected songs
ask again.

An applied repair displays **Overlap repair: Smart** or **Overlap repair: Keep
attacks** beside the preparation state, with affected filenames in its tooltip.
This remains visible when the optional status and preparation controls are
hidden. It describes staged repairs, not the remembered preference, and clears
when those repairs are undone, discarded, or committed with **Save**. Unaffected songs and
**Merge only** do not add a repair indicator.

### Emulator disk-set reports

Disk-set preview shows the actual prepared and packed songs, including musical
change reports and title provenance. Collection edits require an updated preview
before output can be built. See the [disk-set guide](emulator-disk-sets.md).

<a id="distinguish-verification-levels"></a>

## What verification checks

E-SEQ inspection and conversion reject truncated commands, including incomplete
notes, controllers, delays, tempo, meter, and channel-prefix events. A preceding
valid performance does not make a damaged file eligible for partial conversion.
The failed song is reported without creating or staging MIDI output.

Final image verification reopens IMG files or decodes final HFE files and checks
every contained file against the prepared output, including catalogs and song
order. Corrupted final output fails verification.
**Settings → Disk Options → Verify Floppy Contents After Writing** adds a physical
readback comparison. If readback is cancelled,
the result distinguishes completed writing from unverified contents.

Automated tests use self-created songs, temporary FAT12 images, fault injection,
and simulated devices. They validate preparation and delivery behavior. They do
not establish playback compatibility on an actual piano or test a physical USB
floppy drive; those require the controller, firmware, media, and drive combination.

<a id="preserve-recovery-and-extraction-work"></a>

## Keep a partial floppy capture

Floppy discovery runs in separate helper processes with a ten-second wait and
Cancel. APS terminates unfinished helpers on timeout or cancellation while
keeping results from drives that responded. Reconnect an unresponsive drive
and retry: each attempt starts fresh, without requiring APS to restart.

After a failed or cancelled USB recovery read, **Disk → Save Partial Capture...** saves
the captured image and a JSON diagnostic record, including sector coverage and
affected files where the filesystem provides enough evidence. Unread regions
are identified; zero-filled bytes are not recovered song data.

For drive errors, see [Windows disk troubleshooting](windows-disk-troubleshooting.md).

## Resume an interrupted bulk extraction

1. Open **Utilities → Bulk Extraction...** and leave **Save progress for verified
   resume** enabled when starting the extraction.
2. After a failed or cancelled job, keep the original input images, output folder,
   and progress JSON file until extraction is complete.
3. Reopen **Utilities → Bulk Extraction...**, choose **Resume extraction job...**,
   and select the progress JSON file.
4. Check the restored options, then choose **Extract** to resume.

Resuming restores the options, checks that input images are unchanged, and verifies
output hashes before skipping completed songs. Failed images are retried first.
Modified or unrelated destination files are preserved, and newly extracted copies
get unused filenames.
After successful completion, the progress JSON is removed automatically and the
resume suggestion is cleared. Lock files are removed when the job stops. Failed
or cancelled jobs retain their progress JSON for retrying unfinished work. If a
completed record cannot be deleted, the result reports the cleanup problem and
retains its path; resuming it retries cleanup after verifying the outputs.

Related: [preparation profiles](preparation-profiles.md),
[emulator disk sets](emulator-disk-sets.md), and
[rename recovery](rename-recovery.md).
