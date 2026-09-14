# Interrupted filename renames

APS keeps each new rename transaction in a persistent recovery directory, rather
than system TEMP. On startup, an incomplete transaction offers **Restore originals**,
**Resume rename**, or **Later**. Restore returns the files to their original names;
Resume finishes the planned names. Later retains all files and recovery copies.
An unfinished transaction blocks another rename until it has been resolved.

The default location is:

- Windows: `%LOCALAPPDATA%\APS MIDI Prep Tool\rename-recovery`
- macOS: `~/Library/Application Support/APS MIDI Prep Tool/rename-recovery`
- Linux: `$XDG_STATE_HOME/APS MIDI Prep Tool/rename-recovery`, or
  `~/.local/state/APS MIDI Prep Tool/rename-recovery` if that variable is unset.

Set `APS_MIDI_RENAME_RECOVERY_DIR` before launching APS to choose another persistent
location, including a volume with more free space. Keep that setting until all
pending transactions there have been recovered. The error message includes the
recovery location and approximate required space. The estimate includes the moving
files plus allocation and journal overhead; optional user backups need additional
space at their own destinations. Space can change after preflight, so write errors
are still handled without deleting the recovery data.

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

## Verification

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
