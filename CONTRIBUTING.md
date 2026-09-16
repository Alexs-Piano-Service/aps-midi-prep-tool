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

Windows builds require `mformat.exe`, `mcopy.exe`, `mdir.exe`, `mdel.exe`, and
`mren.exe` on the build machine's PATH, or in `aps_midi_prep_tool_app/bin/mtools`
when using `build/build_windows_main.ps1`. The signed workflow installs the
native UCRT64 mtools package through MSYS2. Both build routes include the tools
with `--add-binary`, allowing PyInstaller to collect their DLL dependencies.
CI and signed releases add the setup action's reported `msys2-location` plus
`ucrt64/bin` to PATH; the action can install into a runner temporary directory,
so the runner's preinstalled `C:\msys64` must not be assumed. Both workflows run
`python scripts/check_mtools.py` to report and execute all five required tools
before testing. HFE image tests also require Greaseweazle: CI installs the
pinned v1.23 source on Linux and stages the verified standalone Windows archive
with `scripts/stage_windows_hfe_tool.ps1` in Windows CI and signed releases.
Recipients do not need to install mtools separately. Verify an actual Windows
package on a machine without a development toolchain before distributing it.

The **CI** workflow runs the full suite on Linux and Windows for every branch
push and pull request. Both jobs install mtools and Greaseweazle so disk-image
regression tests exercise actual images. Linux also installs the Qt Multimedia
runtime dependencies, including `libpulse0`. Configure branch protection to require **Tests
(ubuntu-latest)** and **Tests (windows-latest)** before merging.

Before tagging a release, commit the version/documentation changes, push that
exact commit to the repository that will publish the release, and wait for
both CI jobs to pass. Create the tag with the checked helper:

```bash
python scripts/tag_release.py v0.8.3 --repo OWNER/REPOSITORY
git push RELEASE_REMOTE v0.8.3
```

Replace the example tag, repository, and remote with the intended release.
The helper refuses a dirty checkout, missing CI, a failed or unfinished latest
run, and missing/skipped platform jobs. It creates a local annotated tag at the
validated commit and records the CI run URL in the tag message. Use `GH_TOKEN`
or `GITHUB_TOKEN` with Actions read access for a private repository.

The signed Windows release workflow remains responsible for packaging and
signing. It also checks that the exact checked-out commit already passed both
CI jobs, so a manually created tag without recorded validation cannot publish
an executable through that workflow. A local test log alone does not qualify.
The helper does not push tags, configure repository protection, or backfill CI
history for older releases. Administrators can still create tags outside this
helper; repository rules must restrict tag creation if that bypass needs to be
prevented. Publish the release only after the validated tag has been pushed.

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
