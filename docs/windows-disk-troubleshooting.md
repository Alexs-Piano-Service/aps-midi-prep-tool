# Windows image creation and temporary-file warnings

If image creation stops progressing, note the text below the progress bar as
well as the percentage. Different operations use different progress scales.
In the fast floppy reader, 25% is the start of reading song data after the file
map has been read. A pause there can involve the disk or drive. Image preparation
also runs helper programs, so the percentage alone does not identify the cause.

Try saving an IMG to a folder on the computer first. If reading an original
floppy fails, use **Disk → Read Floppy… → Start in recovery mode**. Keep the
original disk write-protected. Recovery diagnostics help distinguish unreadable
sectors from image-preparation errors. Use the error dialog's bug-report option
to include the exact operation and log when reporting a failure.

## Save To Floppy: Windows error 50

If saving reports **The request is not supported**, Windows may have rejected
the free-space query used by file-level saving even though reading the disk as
an image succeeded. Those operations use different access paths. This message
alone does not establish that the disk is damaged or write-protected.

When `GetDiskFreeSpaceExW` returns error 50, APS tries the older
[`GetDiskFreeSpaceW` query](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdiskfreespacew).
Saving proceeds only if the capacity query and directory listing succeed.
Other Windows errors still stop the operation. This compatibility fallback needs
validation with the affected Windows drive and disk.

Bug reports now include a `floppy_save` diagnostic block with the save stage,
both capacity-query results and Windows error codes, whether a change to the
target was attempted, and the numbers of files successfully removed and copied.
It distinguishes a failure before changes from a failure in the final directory
check, which can occur after files have been written. It contains no disk bytes.
A successful directory check does not replace optional readback verification.

If saving still fails, keep the session open and use **Save As Image** to preserve
the prepared disk. Include the failure's bug report so the exact stage and API
results can be checked.

## Changes for stalled operations

- Songs such as `1MOMENT.FIL` can carry the DOS System, Hidden, or Read-only
  attributes. Before replacing or deleting a song in an image, APS clears those
  flags on that entry in its temporary output image. This prevents `mdel` from
  waiting for an invisible protection confirmation. The source image and
  unrelated entries retain their attributes. A timeout here occurs during image
  preparation, before the physical floppy write.
- Drive detection runs in the background with a Cancel button. If a capacity,
  label, or device query does not finish within ten seconds, APS reports it and
  still offers any completed detection results. Reconnect an unresponsive USB
  drive and retry. Retrying reuses an outstanding query so stuck drivers cannot
  create more background threads. A stalled detection query does not prevent
  APS from closing; restarting APS can help when Windows never releases it.
- Image tools such as mformat, mcopy, and the Greaseweazle format converter stop
  after two minutes per command and report which tool timed out. They receive
  no console input, so an unseen command-line question cannot hold up the app.
- Ordinary Windows floppy reads use cancellable I/O. A read request that has
  not completed within 30 seconds requests cancellation and stops with recovery
  guidance. A timeout is not treated as a completed read or zero-filled output.
  Opening a drive and Windows driver cancellation can still take additional time.
- Closing APS during disk work requests cancellation and displays **Stopping
  Disk Work**. Wait for the operation to finish, then close APS again. The app
  keeps its working files available until the disk worker has finished.
- Cancelling an external tool on Windows stops its process tree, including
  children of packaged helpers that could otherwise keep temporary files open.

The Windows bundle includes mtools. A missing-tool error now explains that a
complete APS build is needed; installing a developer toolchain on the recipient's
computer is unnecessary. The resolver checks PATH first and then bundled tool
folders, including `bin/mtools` and `aps_midi_prep_tool_app/bin/mtools`.

## “Failed to remove temporary directory … _MEI…”

The `_MEI…` directory holds unpacked application files. This message comes from
PyInstaller's single-file launcher when it cannot remove those files during
shutdown. A helper process or another program holding a file open can cause it.
It does not establish why a disk copy stalled or whether that copy completed.
Check the saved image and the operation's result before using it.

APS now gives a plain-language explanation when closing during disk work and
waits for that work to stop before allowing exit. The launcher's own warning
runs outside APS's Qt dialogs, after the Python application exits, so these
changes do not replace its wording. If the warning persists after a normal
shutdown, restart Windows and send the error text and APS log with a bug report.

See PyInstaller's documentation on [subprocess lifetime and single-file cleanup](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart)
and its [Windows cleanup issue](https://github.com/pyinstaller/pyinstaller/issues/8701).

These fixes have automated tests for responsive drive detection, its timeout and
cancellation, tool discovery, command timeout/cancellation,
a simulated read stalled at 25%, and resource retention during shutdown. A real
Windows executable and the affected disks still need validation; these tests
cannot determine the physical condition or format of a customer's disk.

## Recovering a failed Windows file save

On Windows, **Save To Floppy** is also available for ordinary loaded files. It
prepares pending edits, conversions, final filenames, and the applicable piano
catalog without constructing a disk image. Unrelated files and folders remain
on the floppy; a confirmation lists matching filenames that will be replaced.
Prepared files are copied through Windows filesystem I/O.

In Image/Floppy Mode, APS still prepares an image, checks the floppy's names and
song bytes against the opened session, reads its actual allocation unit, and
saves a persistent recovery package.
If another disk was inserted or the files changed, read that target again before
saving. To save an image's songs to another disk, use an empty, formatted disk;
replacing an existing disk image remains a separate, explicitly selected write.

All changed songs are staged under temporary `APSxxxxx.TMP` names and checked
before any original song is replaced. Deliberate deletions follow song publication;
`PIANODIR.FIL` is published last. The final file names, sizes, and SHA-256 hashes
must match the prepared image. This mounted-filesystem check is distinct from the
optional physical image readback.

Safe staging needs room for both the old and new files. If there is insufficient
space, APS stops before changing the floppy. Use **Save As Image** to retain the
prepared disk, then deliberately choose an image write to backed-up or spare media.
APS does not delete originals to make staging room or automatically switch write
methods after a denied raw write.

The failure dialog shows the recovery directory. On Windows, the default is
`%LOCALAPPDATA%\APS MIDI Prep Tool\floppy-save-recovery\save-...`.
Each package contains original files, replacements, and a
`manifest.json` mapping the numbered `.bin` files to their original names and
checksums. Image/Floppy Mode also retains `prepared.img`, which can be reopened
to recover the intended song set. For ordinary-file saves, copy replacement
`.bin` files to a local folder using the names in the manifest. Copy original
`.bin` files the same way to recover predecessors.
Keep these copies until the disk has been checked. Packages remain available
after APS closes, including after successful saves; they can be deleted manually
once no longer needed. `APS_FLOPPY_SAVE_RECOVERY_DIR` overrides the location.

Cancellation or failure during staging/publication can leave temporary files or a
partial song set on the disk. APS keeps the recovery package and identifies the
failed phase and completed changes. It does not automatically restore files onto
possibly removed or substituted media. Do not treat a failed save as a usable disk.

## Windows raw writes and verification

Raw writes run in a separate helper, including administrator retries. Cancel is
forwarded while the helper is running. A five-minute operation deadline and a
three-second cancellation grace period bound the helper's work; blocked native
calls trigger helper termination. APS waits for Windows to confirm process exit
before allowing another write or releasing the image. A malfunctioning driver can
still delay termination while Windows cancels pending I/O; this OS limitation is
why APS does not simply abandon an active writer. See Microsoft's
[process termination semantics](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).

Flush error 21 (device not ready) fails the write. Unsupported flushing (error 1)
requires successful physical readback before APS reports success. USB formatting
always reads the target back; a full format compares the complete physical image,
including empty filesystem data, with the prepared image. Capacity fallback probes
use aligned, complete sectors with read deadlines.

Bug reports include the build commit and whether its checkout contained changes,
independent statistics-query and directory results, failure phase, Windows API/error,
staged file/byte counts, and whether mutation was attempted. They include recovery
paths and metadata, not the backed-up disk bytes. These automated checks still
need validation on the affected Windows drive and actual floppy media.
