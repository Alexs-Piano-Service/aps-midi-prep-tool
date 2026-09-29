# Updating this copy of APS MIDI Prep Tool

[All user guides](README.md) · [Getting started](getting-started.md)

Use **Help → Check for Updates…** to check for a newer stable release. Supported
packaged copies offer **Update and restart**. The update preserves your music,
startup configuration, and saved preferences. Nothing is installed until you
choose that action.

<a id="download-restart-and-recovery"></a>

## Install an update

1. Finish active work and close other APS tool windows.
2. Choose **Help → Check for Updates…**, then **Update and restart** if offered.
   Check the application path shown in the dialog: this is the copy that will
   be replaced. Renamed copies and copies on a USB drive are supported.
3. If APS asks about pending edits, choose **Save**, **Discard**, or **Cancel**.
   If saving starts a disk operation, wait for it to finish and try the update
   again.
4. Keep the USB drive connected, if applicable, and wait for APS to download,
   verify, and install the update. You can cancel during preparation.
5. APS restarts from the same path. The previous executable remains beside it
   as `<filename>.previous`.

The application folder must be writable and have room for both the new and
previous copies. The system temporary directory also needs space for the update
helper. The running executable stays unchanged during download and verification.

To control startup reminders, use **Settings → Check for Updates at Startup**.
You can still check manually when this setting is off.

## Supported copies

| Running copy | Update behavior |
| --- | --- |
| Official signed standalone Windows EXE (x86_64) | In-place replacement with the matching official signed EXE. |
| Official Linux AppImage (x86_64 or aarch64) | In-place replacement with an AppImage for the same architecture. |
| Windows folder build, source checkout, unsupported package or platform | Manual download. |

If the dialog offers **Open Download Page**, use it to download the package for
your operating system and follow the [download and launch instructions](getting-started.md).
Copies predating self-update support need one manual replacement first.

## If an update fails

If downloading or verification fails, your existing executable is unchanged.
Check the error details, your internet connection, free space, and whether the
application folder is writable, then try again.

If replacement fails, the helper restores the old executable. If the new
application fails to confirm startup, the helper attempts recovery after the
new process has exited; it never replaces an executable that is still running.
A disconnected or failing USB drive can prevent recovery writes. Reconnect it
before trying recovery.

To restore the previous version manually:

1. Close APS, including the new copy if it is still running.
2. Find `<filename>.previous` beside the application. Keep the failed/new
   executable separately if you need it for diagnosis.
3. Restore the previous copy to the original executable filename. For example,
   restore `My Piano Tool.exe.previous` as `My Piano Tool.exe`, then launch it.

Your startup JSON and music folders require no restoration because the updater
does not replace them. Existing `.previous` backups are retained under unique
suffixes rather than silently overwritten.

Failure details remain in `failure.json` inside the `.aps-update-*` staging
directory beside the executable. Include those details when asking for help.
Successful startup removes temporary update files and retains the previous
application.

## How supported copies are recognized

Windows updater releases contain `APSMIDIPrepTool.exe`; Linux releases contain
`APSMidiPrepTool-VERSION-ARCH.AppImage`. Current packages declare their format
and architecture in the build identity. Builds that include the updater but
lack these markers can still qualify when their running bundle is recognized
as a supported standalone Windows EXE or Linux AppImage. Renaming an executable
is supported; renaming an installer or an arbitrary program does not make it
eligible.

APS also keeps a small `<filename>.update-lock` file to prevent two running
copies from updating the same application at once. It continues to work when
the USB drive's letter or mount point changes.

## Verification and trust

The existing HTTPS update feed only announces a version. The updater resolves
the exact stable `vVERSION` release in the pinned repository
`Alexs-Piano-Service/aps-midi-prep-tool`, rejects draft/prerelease assets, chooses
the supported package and architecture, and downloads through GitHub's HTTPS
release infrastructure. It checks the downloaded bytes against the SHA-256
digest supplied by the official release asset metadata. An absent or mismatched
digest prevents installation. GitHub documents this field in its
[release asset API](https://docs.github.com/en/rest/releases/assets).

Windows adds Authenticode verification: both the installed executable and the
download must have valid signatures from the same publisher. An unsigned copy,
invalid signature, or different publisher cannot update automatically. The
check uses Windows' signature validation through
[Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature?view=powershell-7.5).
It uses the existing release signing workflow and introduces no new signing
keys. A publisher identity change requires a manual update.

AppImage verification relies on HTTPS and the asset digest from the official
GitHub repository. There is no detached AppImage signature or independent
offline signing key. This protects against corrupted downloads and untrusted
download locations, but does not provide an independent defense if the official
repository's release publishing authority is compromised.

The helper and restarted application use a fresh process environment appropriate
to their packaging. Frozen applications can otherwise pass their temporary
library paths to child processes; see
[PyInstaller's subprocess guidance](https://pyinstaller.org/en/latest/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application).
Publication and acceptance steps are in the [release process](release-process.md).
