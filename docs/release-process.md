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
are **0.8.6 development**, not an update to the existing v0.8.3 packages.

The 0.8.6 package list requires the Windows installer, portable ZIP,
Linux AppImage, and Windows test kit. Their exact filenames are in the metadata.
The signed Windows workflow produces a standalone EXE and test kit. It does
not produce the other required packages. Build and upload them before release,
or explicitly change the declared package set in the validated release commit.
An incomplete draft remains private if a build or acceptance test fails.

Use accepted artifacts for the validated commit where available. Test the final
signed/archived packages; rebuilding or signing after testing changes their
hashes and requires acceptance again. Run the installer and portable package
on clean Windows 10 and Windows 11 machines, and the AppImage on clean Linux.
Follow the complete Windows test plan, including MP3 rendering, and retain its
results and environment records. For Linux, verify launch, IMG creation and
reopening, an HFE round trip, and MP3 rendering without development tools.

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
  "tag": "v0.8.6",
  "commit": "FULL_VALIDATED_COMMIT_SHA",
  "packages": [
    {
      "asset": "APSMIDIPrepTool-0.8.6-windows-portable.zip",
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
