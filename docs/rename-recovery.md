# Interrupted filename renames

[All user guides](README.md) · [Getting started](getting-started.md)

If APS closes unexpectedly during a filename rename, reopen APS to recover it.
Keep the original music drive connected and leave any temporary files in place
until recovery finishes. APS keeps recovery copies in a persistent folder, so
they remain available after the application closes.

## Choose how to recover

The **Recover interrupted rename** dialog appears at startup. Choose the result
you want:

| Choice | Result |
| --- | --- |
| **Restore originals** | Returns the files to their original names. |
| **Resume rename** | Finishes the planned filename changes. |
| **Later** | Leaves files in their current locations and keeps recovery copies. Reopen APS to try again. |

Wait for **Rename recovery complete** before opening the files. APS removes the
recovery copies after successful recovery. An unfinished rename blocks another
rename until it has been resolved.

## If recovery cannot finish

Open the error dialog's details and keep the recovery folder. Check that the
original drive is connected, the files and recovery folder are accessible, and
another APS instance is not still working on the rename. Close the other instance
normally before retrying.

Unexpected or changed files stop recovery; APS does not overwrite those conflicts
automatically. Do not delete temporary files or recovery copies to clear the
message. If the problem continues, use **Help → Report a Bug…** and include the
error details and recovery location. Recovery can be retried after another
interruption.

## Find the recovery copies

The startup recovery dialog includes the folder path in its details. The default
locations are:

- Windows: `%LOCALAPPDATA%\APS MIDI Prep Tool\rename-recovery`
- macOS: `~/Library/Application Support/APS MIDI Prep Tool/rename-recovery`
- Linux: `$XDG_STATE_HOME/APS MIDI Prep Tool/rename-recovery`, or
  `~/.local/state/APS MIDI Prep Tool/rename-recovery` if that variable is unset.

## If there is not enough recovery space

The error message includes the recovery location and approximate required space.
Free space there before trying again. For an advanced setup, set the environment
variable `APS_MIDI_RENAME_RECOVERY_DIR` before launching APS to choose another persistent
location, including a volume with more free space. Keep that setting until all
pending renames there have been recovered. Changing this setting does not move
existing recovery copies. The estimate includes the moving
files plus allocation and journal overhead; optional user backups need additional
space at their own destinations. Space can change after preflight, so write errors
are still handled without deleting the recovery data.

## Technical details

Staging uses exclusively reserved `.aps_midi_rename_<UUID>_<index>.tmp` files beside
originals. It does not create subdirectories in source folders. APS still needs
permission to read originals, create staging files, rename files, and write recovery
copies. It cannot safely bypass a denial of those operations.

Each transaction contains independent, flushed snapshots with SHA-256 digests and
an atomically updated journal. The journal records each move's intent before the
move, then records its new location afterward. Recovery resolves the interrupted
move from the files' locations and contents. Both recovery actions can be restarted
after a second process death. A process lock prevents another APS instance from
recovering a live transaction. Unexpected or changed files stop recovery and retain
all snapshots for inspection; APS does not overwrite those conflicts automatically.

Snapshots and journals use Python's [file flushing API](https://docs.python.org/3.10/library/os.html#os.fsync).
Directory changes are also flushed where supported. The subprocess tests exercise
hard process termination, not a physical power cut. Power-loss durability depends
on the filesystem, network share, and storage hardware honoring flushes. Older
version-1 recovery snapshots created by previous builds in system TEMP are not
migrated by this journal format; keep those copies for manual restoration.

<a id="verification"></a>

## Maintainer verification

```sh
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests/test_dos83_renamer.py tests/test_rename_process_recovery.py tests/test_filename_policy.py
```

The subprocess tests call `os._exit()` after every staging and publication position,
including before the location journal is updated. They cover chains, swaps, cycles,
and independent renames, both recovery choices, and a second hard exit during
recovery. Additional tests cover interrupted preparation, conflicting files, process
locking, corrupted journals, insufficient space, failed writes, unavailable TEMP,
and denied source-directory creation.

On Windows, the native ACL test denies `AD` on the source directory without
inheritance, verifies that creating a subdirectory fails, and runs the rename.
Microsoft documents `AD` as the directory-creation right in
[icacls](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls).
This test is skipped on other platforms. Run it on Windows/NTFS before release.
