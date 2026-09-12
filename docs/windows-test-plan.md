# Windows IMG/HFE release acceptance

Test the exact packaged EXE on clean Windows 10 and Windows 11 machines or VMs.
No Python, Git, mtools, MSYS2, or other development tools should be installed.
The automated source tests in `tests/windows` do not establish that the release
EXE includes all of its dependencies. This manual pass remains a release gate.

## Get the test kit

Download `windows-test-kit` from the successful GitHub Actions **CI** run for
the commit being tested. Extract the ZIP. Download and extract/install the
Windows application release separately. Keep its entire distribution together.
For a one-folder release, selecting just the EXE without `_internal` is invalid.
Test the installed build and the single-file build separately if both ship.

From Windows PowerShell in the extracted kit:

```powershell
.\Start-ManualTest.ps1 -AppExe 'C:\APS\APS MIDI Prep Tool.exe' -RequireCleanMachine -Launch
```

If the downloaded script is blocked, review it and use `Unblock-File` on that
script. If local policy disables scripts, invoke just this reviewed script with
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-ManualTest.ps1`
followed by the same arguments. No machine-wide execution-policy change is needed. The script checks
fixture SHA-256 hashes, copies the fixtures into a new results folder, records
the EXE hash and signature status, and launches it with a Windows-only PATH.
It restores the shell's PATH after the app exits. Run every test against the
copies under `results-...\work`, and save outputs in the results folder.

`-RequireCleanMachine` rejects external image tools found on PATH. Python and
Git paths are recorded for review (a Windows Python Store alias is not itself
a Python installation). Also confirm manually that MSYS2, Python, Git, and
development environments are absent; PATH inspection alone cannot
prove a clean installation. Omitting the switch allows a development-machine
smoke check, which must be recorded as such. Without `-Launch`, only fixture
and environment preflight runs. Neither mode marks any manual case as passed.

Record Windows edition/build, app version, release URL/tag, package type,
tester, date, and hardware in `results.csv` notes. `environment.json` captures
the executable's path, hash, signature status, and available commands.

## Cases

| # | Action | Required result |
| --- | --- | --- |
| 1 | Launch the packaged EXE; inspect File, Disk, and Utilities menus. | Normal window, no missing DLL/tool prompt or unexpected console. Image commands available. |
| 2 | Load `work\songs` (three distinct titles), select a 720 KB Disklavier destination, save `test720.img`, and reopen it. | Exactly three songs with correct titles/order; IMG is 737,280 bytes; free space and catalog are reasonable. |
| 3 | Rename a song, edit a title, delete a second song, and add another if supported. Use ordinary Save, exit APS completely, restart the same EXE, and reopen. | Changes persist; no duplicates, catalog damage, or missing unrelated songs. |
| 4 | Open `work\images\mixed720.img`, change only SONG1.FIL's title, and use ordinary Save. Exit and reopen. | SONG1/SONG2 and PIANODIR.FIL remain valid. NOTES.TXT, EXTRA.DAT, PSONG.MNG, and PDISK.MNG remain byte-for-byte intact. Compare extracted sidecars with `work\mixed-source` using `Get-FileHash`. The MNG fixtures are deliberately opaque preservation data, not valid catalogs. |
| 5 | From the mixed image use clean E-SEQ Save As Image/export to `clean720.img`, then reopen it. | Only intended E-SEQ songs and PIANODIR.FIL remain. The original mixed image retains its unrelated files. |
| 6 | Load the three MIDI songs, select Nalbantov/HFE output, and build `DSKA0000.HFE` in the results folder. | Nonzero HFE created with no dependency prompt. Do not overwrite the supplied fixture. |
| 7 | Open the generated HFE and the supplied `work\images\DSKA0000.HFE`. | Generated image has the three source songs; supplied image has two E-SEQ songs and sidecars. Correct titles, playback order, and 720 KB/DD capacity. |
| 8 | Edit a title in the generated HFE; delete or replace a song if supported. Save as `DSKA0001.HFE`, exit, restart, and reopen. | Edits persist; all intended songs remain readable. |
| 9 | Export `test720.img` as `from-img.HFE` and reopen. | Same intended songs, bytes, titles, and order. |
| 10 | Export the generated HFE as `from-hfe.img` and reopen. | Same intended songs, bytes, titles, order, and 720 KB capacity. |
| 11 | In MIDI mode, create IMG and HFE with `work\capacity\NEARFULL.MID`, then try `TOOBIG.MID`. Set the emulator builder's safety margin to zero for this boundary check. Also try adding beyond capacity to a single existing disk. | Near-full succeeds. A single oversized song fails clearly and preserves existing output. Multi-image builders may legitimately split a set: verify every image instead of expecting failure. E-SEQ conversion can remove padding, so use MIDI mode for these fixtures. |
| 12 | Build from `work\filename-cases`. Try output set names CON, AUX, NUL, COM1, LPT1, CON.mid, and AUX.mid. | Exactly the long-name MIDI, Test Song.mid, and valid NOEXTENSION are selected; backups, temps, and dot files are excluded. Output folder names are usable. Windows normally aliases Test Song.mid/test song.mid and cannot create ordinary CON.mid/AUX.mid input files: test these as requested output names, not separate host files. |
| 13 | Open `work\images\damaged-boot.img` and `truncated.img`; use available recovery/reconstruction, extracting to a new Recovered folder. Reopen reconstructed output if offered. | No hang. Boot-damaged fixture has intact songs/catalog/FAT and should recover; severely truncated data may fail with useful diagnostics. No silent claim that unread data is valid; damaged sources remain unchanged. |
| 14 | Start a sufficiently large IMG/HFE build, cancel during work, and repeat during recovery where supported. | Prompt return, unchanged sources/existing outputs, no persistent mformat/mcopy/gw descendants. Incomplete output is removed or clearly identified. |
| 15 | Close APS during disk activity using disposable images. Repeat a normal close after completion. | Safe wait/cancel/explanation; helper processes terminate; no recurring `_MEI...` cleanup warning. A Task Manager force-kill is a separate crash test and cannot require normal cleanup guarantees. |
| 16 | Make a disposable output unavailable or hold it open in another application; save, then release the lock and retry. | Failure is reported, existing source/output remains intact, and retry works. |
| 17 | If hardware is available, read a known-good 720 KB floppy and reopen IMG/HFE copies. Cancel a marginal-disk recovery and retain partial capture/diagnostics. | Usable good capture; bounded stalls/cancellation; unread sectors reported. Mark N/A with hardware reason when unavailable. Never write to original disks. |
| 18 | After restarting, repeat create/open/edit IMG, create/open HFE, both conversions, damaged-image recovery, and cancellation. Run `where.exe mformat` (also mcopy, mdir, mdel, mren) outside APS. | All workflows still work; tools are still absent from the machine PATH. |

## Release decision

Complete every row in `results.csv` with PASS, FAIL, or an explained N/A. Leave
unexecuted cases as NOT RUN. Retain the environment record, outputs, screenshots
of failures, and recovery diagnostics with the release evidence. Passing CI or
preflight alone does not satisfy clean-machine acceptance.

Block release for missing bundled tools/DLLs; dependency installation required
on the test PC; unreadable generated IMG/HFE; ordinary Save losing sidecars;
persistent helper processes; failed saves corrupting originals; false success
on overflow; round-trip song loss; or recurring temporary-file cleanup errors.

## Developer automation

On a Windows development machine, install `requirements-test.txt`, native mtools,
the Greaseweazle CLI, and MSYS2's `mingw-w64-ucrt-x86_64-lame` package. Stage
the encoder from your MSYS2 installation, then run from the repository root:

```powershell
./scripts/stage_windows_lame.ps1 -SourceDirectory C:/msys64/ucrt64/bin -Destination "$env:TEMP/aps-lame"
$env:APS_WINDOWS_LAME_DIR = "$env:TEMP/aps-lame"
python -m pytest -q tests/windows --windows-require-tools --junitxml=test-results/windows.xml
python -m pytest -q --ignore=tests/windows
python -m scripts.build_windows_test_kit --output dist/windows-test-kit
```

Optionally set `APS_WINDOWS_MTOOLS_DIR` to the directory holding all five EXEs
and their DLLs, and `APS_WINDOWS_GW_DIR` to an extracted standalone Greaseweazle
release folder containing `gw.exe` and its runtime. mtools is copied into a
temporary bundle layout and removed from PATH during tests. With GW_DIR set,
the complete Greaseweazle folder is copied too. Otherwise HFE integration uses
the absolute path of an installed CLI. CI downloads the official standalone
Greaseweazle 1.23 Windows archive, checks its pinned SHA-256, and uses GW_DIR.
CI and release builds install LAME through MSYS2 and stage its EXE, runtime DLLs,
and documentation with the same script. The audio test copies that bundle to a
path containing spaces and an accented character, removes MSYS2 from PATH, and
uses the application's export function to encode a real WAV file as MP3. The
release packages the tested directory at `bin` inside the frozen application.
These tests exercise source code, not a frozen APS executable.

On Windows the strict option makes missing image tools or staged LAME fail. Without it,
tool-dependent cases explicitly skip; native handle/process tests still run.
On other platforms the Windows directory skips. Existing cross-platform tests
continue to cover catalog reconstruction, retained recovery diagnostics, GUI
shutdown decisions, and conversion logic. No automated test touches a physical
drive. CI publishes the portable kit and separate JUnit results as artifacts.
