param(
    [string]$PyInstaller = "pyinstaller"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

& $PyInstaller --noconfirm --windowed `
    --name "APS MIDI Prep Tool" `
    --icon (Join-Path $RepoRoot "aps_midi_prep_tool_app\aps.ico") `
    --manifest (Join-Path $RepoRoot "manifests\main_as_invoker.xml") `
    (Join-Path $RepoRoot "aps_midi_prep_tool.py")
