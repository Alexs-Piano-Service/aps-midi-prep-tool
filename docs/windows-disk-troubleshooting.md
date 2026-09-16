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

## Changes for stalled operations

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
