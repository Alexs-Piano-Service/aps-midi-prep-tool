# Updating this copy of APS MIDI Prep Tool

Supported packaged copies offer **Update and restart** when a newer stable
release is available. The update replaces the executable you launched, including
a renamed copy on a USB drive, and reopens that same path. It preserves music,
startup configuration, and saved preferences. Nothing is installed until you
choose the update action.

## Supported copies

| Running copy | Update behavior |
| --- | --- |
| Official signed standalone Windows EXE (x86_64) | In-place replacement with the matching official signed EXE. |
| Official Linux AppImage (x86_64 or aarch64) | In-place replacement with an AppImage for the same architecture. |
| Windows folder build, source checkout, unsupported package or platform | Manual download. |

Windows updater releases contain `APSMIDIPrepTool.exe`; Linux releases contain
`APSMidiPrepTool-VERSION-ARCH.AppImage`. Current packages declare their format
and architecture in the build identity. Builds that include the updater but
lack these markers can still qualify when their running bundle is recognized
as a supported standalone Windows EXE or Linux AppImage. Renaming an executable
is supported; renaming an installer or an arbitrary program does not make it
eligible. Copies predating self-update support need one manual replacement first.

## Download, restart, and recovery

1. Finish active work and close other tool windows. APS checks for pending
   edits before restarting; you can save, discard, or cancel as usual.
2. APS downloads to a separate staging directory beside the executable, with
   progress and cancellation. The running executable remains unchanged while
   the download and verification take place.
3. A separate helper waits for APS to exit before replacing it. Keep the USB
   drive connected. The destination must be writable and have room for the
   new application and previous copy; the system temporary directory also
   needs room for the helper copy.
4. APS reopens from its existing path. The prior executable remains beside it
   as `<filename>.previous`. An existing backup is retained under a unique
   suffix rather than silently overwritten.

APS also keeps a small `<filename>.update-lock` file to prevent two running
copies from updating the same application at once. It continues to work when
the USB drive's letter or mount point changes.

If replacement fails, the helper restores the old executable. If the new
application fails to confirm startup, the helper attempts recovery after the
new process has exited; it never replaces an executable that is still running.
Failure details remain in `failure.json` inside the `.aps-update-*` staging
directory beside the executable. Successful startup removes temporary update
files while retaining the previous application.

For manual recovery, close APS and restore `<filename>.previous` to the
original executable filename. For example, restore `My Piano Tool.exe.previous`
as `My Piano Tool.exe`. Keep the failed/new copy separately if you need it for
diagnosis. Startup JSON and music folders require no restoration because the
updater does not replace them. A disconnected or failing USB drive can prevent
recovery writes; reconnect it before trying recovery.

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
