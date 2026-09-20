# Contributing

Thanks for helping improve APS MIDI Prep Tool.

## Ground Rules

- Test with copies whenever possible. Do not risk the only copy of a floppy,
  disk image, or customer file.
- Do not upload copyrighted disks, commercial MIDI libraries, proprietary
  firmware, private customer files, or other material you do not have the right
  to share.
- Keep bug reports focused on reproducible behavior. If a sample file is needed,
  use a public-domain or self-created file, or coordinate privately first.
- Be careful with physical floppy operations. Formatting and writing can destroy
  data on the selected disk.
- Refer to third-party products, formats, and trademarks only for compatibility
  or preservation context. Do not imply endorsement or affiliation with their
  owners.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6 certifi
python3 aps_midi_prep_tool.py
```

Image, floppy, and audio workflows may also need `mtools`, the Greaseweazle CLI,
FluidSynth with a redistributable SoundFont, and LAME.

## Before Submitting Changes

Install the test dependencies, then run the release checks:

```bash
python -m pip install -r requirements-test.txt
make release-check
```

This also runs the full automated test suite and validates release-version and
translation-catalog consistency. When changing floppy, image, E-SEQ, or MIDI
conversion behavior, test with copies of representative files and note what
workflow you verified.

## CI and release tags

Both local Windows builds and the signed workflow invoke
`build/build_windows_main.ps1`. The script requires all five mtools executables
(`mformat`, `mcopy`, `mdir`, `mdel`, `mren`) on PATH or in the bundled mtools
folder. PyInstaller collects their DLL dependencies. The workflow obtains
mtools from the MSYS2 setup action's reported `msys2-location`, never a presumed
`C:\msys64` path. Run `python scripts/check_mtools.py` before testing.

HFE support is required. `scripts/stage_windows_hfe_tool.ps1` downloads the
pinned, SHA-256-verified standalone Greaseweazle archive and records all extracted
runtime files. The shared build verifies that receipt and includes the complete
library, DLLs, and licenses. With no `APS_WINDOWS_GW_DIR` or
`-GreaseweazleDirectory`, local builds run the same staging script automatically.
A lone `gw.exe` or an incomplete/modified stage fails the build.

LAME uses the complete runtime and documentation staged by
`scripts/stage_windows_lame.ps1`. Set `APS_WINDOWS_LAME_DIR` or `-LameDirectory`
to that stage; otherwise the build stages the MSYS2 installation containing
`-LameExe` or the `lame.exe` found on PATH. `-BundleLame $false` is only for
special-purpose development builds, which cannot pass release acceptance.
`-OneFile -Name APSMIDIPrepTool` selects the signed workflow's EXE layout;
the default local layout remains a folder named `APS MIDI Prep Tool`.

The **CI** workflow runs the full suite on Linux and Windows for every branch
push and pull request. Both jobs install mtools and Greaseweazle so disk-image
regression tests exercise actual images. Linux also installs the Qt Multimedia
runtime dependencies, including `libpulse0`. Configure branch protection to require **Tests
(ubuntu-latest)** and **Tests (windows-latest)** before merging.

In `Alexs-Piano-Service/aps-midi-prep-tool`, successful push CI on `main`
automatically starts **Release Windows (Signed)** for the same commit. Download
`APSMIDIPrepTool-windows-signed` from that run's Artifacts section for the signed
EXE and Windows manual test kit. The workflow also exercises the signed EXE's
window, IMG creation/reopening, HFE round trip, and built-in piano MP3 rendering
with development tools removed from PATH. Its `windows-package-smoke` artifact
records the EXE hash and results. This is still a hosted runner, so clean Windows
10 and 11 acceptance remains required. Automatic signing uses the public
repository's Azure configuration and is skipped for other repositories and PRs.

`packaging/release.json` declares the version, publication status/date, and exact
required package filenames. During development, README and the consolidated
changelog identify 0.8.5 as unreleased; AppStream identifies a dated development
snapshot. Before tagging, set the actual release date and `status: ready`, change
the README to `Current version`, date the changelog entry, and set AppStream's
matching stable release date. Do this in one commit, push it, and wait for both
CI jobs. The tagging helper rejects version/tag mismatches and unreleased or
inconsistent metadata in addition to missing, failed, or skipped CI:

```bash
python scripts/tag_release.py v0.8.5 --repo OWNER/REPOSITORY
git push RELEASE_REMOTE v0.8.5
```

The helper creates a local annotated tag and records its CI URL. It does not
push. Packaging validates the same metadata, including tags supplied directly
to the workflow. Use `GH_TOKEN` or `GITHUB_TOKEN` for private Actions access.

Prepare a **draft** and publish after every intended package is uploaded and
accepted. The Windows workflow no longer runs on `release: published`; a manual
run's optional `release_tag` stages its EXE and test kit in a draft. It does not
produce the installer, portable ZIP, or Linux AppImage: build/stage those too,
or deliberately revise the required package list before tagging. Already
accepted build artifacts can be staged directly without rebuilding:

```bash
python scripts/release_assets.py stage v0.8.5 --repo OWNER/REPOSITORY --files dist/PACKAGE
python scripts/release_assets.py publish v0.8.5 --repo OWNER/REPOSITORY --acceptance dist/release-acceptance.json
```

Publication checks the exact remote tag and CI commit, every declared asset,
and clean-machine acceptance tied to each uploaded package's SHA-256.
See [release evidence](docs/release-process.md) for the record format and
[Windows acceptance](docs/windows-test-plan.md) for the actual tests.
Both staging and the legacy signing workflow refuse to modify a published
release. Repository administrators can still bypass helpers using GitHub's UI
or API; restrict release/tag permissions if that must be prevented.

The gate uses GitHub [workflow run](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow)
and [workflow job](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run)
APIs to check commit identity and both test results.

## Documentation

For Windows IMG/HFE changes, run the native suite and the packaged-release
acceptance checks in [Windows test plan](docs/windows-test-plan.md). CI publishes
a portable fixture kit and JUnit results; testers need only the packaged EXE
and Windows PowerShell on a clean machine. Missing real tools must fail the
automated release run (`--windows-require-tools`), not silently skip it.

Keep `README.md` user-focused, keep `CHANGELOG.md` updated, and update
`aps_midi_prep_tool_app/eseq_reference.md` when E-SEQ behavior changes.

## License

By contributing, you agree that your contribution is provided under the Apache
License, Version 2.0.
