# Release packages and acceptance evidence

Keep `packaging/release.json` consistent with the application version, README,
changelog, and AppStream metadata. `unreleased` has no publication date;
`development_date` dates only the AppStream development snapshot. On release
day, use `ready`, set `date` to the actual release date, and update the matching
documentation. The tagging helper and packaging workflow check this identity.

Before tagging, remove the README's planned-changes wording as well as changing
its version label; the metadata check rejects a released README that still calls
its changes planned. After publication, update the version badge, latest-version
text, release date, and release-notes link on the
[download page](https://www.alexanderpeppe.com/aps-midi-prep-tool/) together.
Check them against the published GitHub release, then advance this checkout to
the next unreleased patch version with a new changelog section. Do not move an
already published tag to repair its documentation.

The September 19, 2026 audit found the download page still showing v0.8.2 and
September 9 while GitHub's latest release was
[v0.8.3, published September 18](https://github.com/Alexs-Piano-Service/aps-midi-prep-tool/releases/tag/v0.8.3).
The website is maintained separately from this repository; its current release
labels need to say **v0.8.3** and **September 18, 2026**. The pending fixes here
are **0.8.9 development**, not an update to the existing v0.8.3 packages.

The 0.8.9 package list requires the signed standalone `APSMIDIPrepTool.exe`,
Windows installer, portable ZIP, Linux AppImage, and Windows test kit. Their
exact filenames are in the metadata. The standalone EXE is the Windows
self-update payload and must be present even when the installer and ZIP exist.
Keep that exact standalone filename for v0.8.7 clients: their installed updater
does not recognize `APS.MIDI.Prep.Tool.exe`. The new updater accepting both names
does not change the old executable's selection logic. Upload only one accepted
standalone name, since multiple candidates are rejected as ambiguous.
The signed Windows workflow produces a standalone EXE and test kit. It does
not produce the other required packages. Build and upload them before release,
or explicitly change the declared package set in the validated release commit.
An incomplete draft remains private if a build or acceptance test fails.

Use accepted artifacts for the validated commit where available. Test the final
signed/archived packages; rebuilding or signing after testing changes their
hashes and requires acceptance again. Run the installer and portable package
on clean Windows 10 and Windows 11 machines, and the AppImage on clean Linux.
Include the standalone EXE in Windows 10 and Windows 11 acceptance records.
Follow the complete Windows test plan, including MP3 rendering, and retain its
results and environment records. For Linux, verify launch, IMG creation and
reopening, an HFE round trip, MP3 rendering, and refusal to overwrite an image
changed outside the app, without development tools.

The Linux build script runs `--aps-package-smoke` from the completed AppImage
before writing its checksum. Its `image_source_changes` case adds a file to an
already-open disposable IMG, checks that saving refuses without changing the
newer image, then checks recovery to a new file and saving after reload. This
guards against distributing an older binary even when source tests pass.
Results and a SHA-256 of the complete AppImage are retained under
`build/appimage/acceptance-VERSION-*/`. A failure stops the build before it
creates a release checksum. These local checks do not establish clean-machine
acceptance or change an unreleased version's publication status.

The default package checks cover the five released cases: `ui`, `img`,
`image_source_changes`, `hfe`, and `mp3`. The Smart PianoSoft synchronization
utility is deferred from 0.8.9, and its retained `smart_pianosoft` case is an
optional development check. Opt in with
`--aps-package-smoke OUTPUT --include-smart-pianosoft`, or add
`-IncludeSmartPianoSoft` to the Windows package-test script. It uses an original
synthetic recording and MIDI fingerprints to check the loaded song-list path,
source preservation, synchronization, and encoded WAV channels without a CD
drive or commercial music. This does not enable the utility in the release UI.
Linux builds omit the CD reader by default (`BUNDLE_CDDA=0`); developers can
include it with `BUNDLE_CDDA=1`. Physical Disklavier playback remains a future
[development acceptance check](playback-and-audio.md#smart-pianosoft-development-deferred).

For an offline rebuild with the existing, populated build environment:

```bash
PIP_NO_INDEX=1 GREASEWEAZLE_REQUIREMENT=greaseweazle==1.23 ./scripts/build_appimage.sh
```

This requires all build dependencies and appimagetool to be installed already.
Keep source and artifact verification separate: the local 0.8.7 AppImage was
built before the source-change safeguard landed. Until an installed package
passes that case, reopen images after external edits or repairs, or recover
pending edits with **Save As Image** to a new filename.

The signed EXE also runs an automated smoke check after packaging. Run that
check locally on Windows with:

```powershell
./scripts/test_windows_package.ps1 -ExecutablePath dist/APSMIDIPrepTool.exe -OutputDirectory C:/Temp/aps-package-smoke
```

This removes development tools from PATH, enforces a deadline, and records the
actual executable hash. Passing on a development machine or hosted CI runner
does not establish clean-machine acceptance.

Save a release acceptance record outside tracked source (for example,
`dist/release-acceptance.json`). Use the **package asset's** SHA-256 for each
record, including the ZIP hash when testing an extracted portable distribution.
Keep the executable hash in the linked Windows environment record as well.
Provide one record per package/platform; test kits need no separate acceptance.
Any extra uploaded EXE, ZIP, or AppImage also needs acceptance.

```json
{
  "tag": "v0.8.9",
  "commit": "FULL_VALIDATED_COMMIT_SHA",
  "packages": [
    {
      "asset": "APSMIDIPrepTool-0.8.9-windows-portable.zip",
      "sha256": "SHA256_OF_UPLOADED_ZIP",
      "platform": "windows-11",
      "clean_machine": true,
      "tester": "Tester name",
      "evidence": "Location of retained results.csv, environment.json, and outputs",
      "checks": {
        "launch": "PASS",
        "img_create_reopen": "PASS",
        "hfe_roundtrip": "PASS",
        "mp3_render": "PASS"
      }
    }
  ]
}
```

Repeat for `windows-10` and each other Windows package; use `linux` for the
AppImage. Enter PASS only after performing the checks. The helper validates
the recorded identity, completeness, and asset digests; it relies on the tester
for truthful evidence. Retain this record with the release evidence.

Stage packages with `scripts/release_assets.py stage`, then use its explicit
`publish` command as shown in CONTRIBUTING. Staging creates or updates a draft;
publication refuses missing uploads, incomplete metadata, wrong tag/commit,
missing CI, or mismatched/absent clean-machine evidence. Existing assets are
not silently replaced. Update release notes while still in draft if needed.

The helpers use GitHub's documented [draft release commands](https://cli.github.com/manual/gh_release_create)
and [explicit publication command](https://cli.github.com/manual/gh_release_edit).
Direct publication through GitHub can bypass these checks; repository access
controls remain the boundary for who can publish.

## Self-update publication and acceptance

Build using `build/build_windows_main.ps1 -OneFile -Name APSMIDIPrepTool` or
`scripts/build_appimage.sh`. These scripts embed `build-info.json` alongside
the application's Python modules with `package_kind` (`windows-onefile` or
`linux-appimage`) and `architecture` (`x86_64` or `aarch64`). Windows folder
builds embed `windows-onedir` and use manual updates. Source builds and
unsupported packages also use manual updates. A build that includes the updater
but lacks markers can qualify when runtime packaging evidence identifies a
supported standalone Windows EXE or AppImage; a filename alone is insufficient.
Copies predating the updater still need one manual replacement first. Do not
copy a marker from another build. The legacy `write_build_info.py DESTINATION`
command continues to write source identity alone; repository build scripts
always pass an explicit package kind and embed the full identity.

Sign the Windows payload through the existing Trusted Signing workflow. The
running EXE and replacement must both have a valid Authenticode signature from
the same publisher. A publisher identity change needs a manual transition;
do not bypass that check. Publish the exact accepted bytes under the stable
`vVERSION` tag in `Alexs-Piano-Service/aps-midi-prep-tool`, with a GitHub asset
SHA-256 digest. AppImages use the digest from the official release over HTTPS;
this repository does not provide detached AppImage signatures. See the
[updater trust contract](self-updates.md#verification-and-trust).

Only after the stable GitHub release is public and all required assets are
available, set `latest_version` in the separately maintained
`https://www.alexanderpeppe.com/aps-midi-prep-tool-data/update.json` to that exact
version (without `v`). Keep its release-notes/download links consistent with
the published release. The feed announces a version; it cannot supply an
arbitrary executable URL. Drafts, prereleases, and incomplete assets are not
eligible for automatic installation. Changing the feed is a separate website
publication step; the repository's build and test commands do not do it.

Before announcing the first updater release and every packaging change, test
an update from the previous supported signed EXE or AppImage on a removable
drive. Keep a copy renamed by the user and verify the same path and filename
restart, startup JSON and music files remain byte-for-byte unchanged, preferences
survive, and the previous executable remains available. Also test download
cancellation/failure, invalid digest, invalid/mismatched Windows signer,
insufficient space or write permission, unsaved edits, active disk work, and
replacement/restart failure with recovery. Include native Windows 10/11 and
Linux results in the retained acceptance evidence. Existing copies built
before updater support require one manual replacement to gain this feature.

For the v0.8.7 transition, start with the actual released signed executable,
not a rebuilt copy of its source. Test both its original local filename and a
renamed copy in a USB folder containing spaces. Record the old and new hashes,
downloaded asset name, restart path, and displayed application version. Source
tests and a new executable's own updater cannot establish old-client acceptance.

Run several successive successful updates and verify that each leaves exactly
one current `.previous` recovery copy and no completed `.aps-update-*` directory.
Backups left by older updater versions may remain; record them before testing.
Cancel a download and exercise read-only, full, and disconnected disposable
destinations. The working executable and adjacent customer files must remain
intact, or recovery must be clearly reported and retained when the drive is
unavailable. Failed/unfinished update directories must not be removed by the
successful-update retention policy. Never simulate drive failures on customer
media. These native checks remain unperformed until their evidence is recorded.
