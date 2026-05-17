param(
    [string]$PyInstaller = "pyinstaller"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$PyInstallerArgs = @(
    "--noconfirm",
    "--windowed",
    "--name", "APS MIDI Prep Tool",
    "--icon", (Join-Path $RepoRoot "aps_midi_prep_tool_app\aps.ico"),
    "--manifest", (Join-Path $RepoRoot "manifests\main_as_invoker.xml")
)

$GreaseweazleExe = Join-Path $RepoRoot "aps_midi_prep_tool_app\bin\greaseweazle\gw.exe"
if (Test-Path $GreaseweazleExe) {
    $PyInstallerArgs += @(
        "--add-binary",
        "$GreaseweazleExe;aps_midi_prep_tool_app\bin\greaseweazle"
    )
}

& $PyInstaller @PyInstallerArgs (Join-Path $RepoRoot "aps_midi_prep_tool.py")
