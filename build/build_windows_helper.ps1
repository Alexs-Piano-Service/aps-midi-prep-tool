param(
    [string]$PyInstaller = "pyinstaller"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

& $PyInstaller --noconfirm --console `
    --name "aps-format-helper" `
    --icon (Join-Path $RepoRoot "aps_midi_prep_tool_app\aps.ico") `
    --uac-admin `
    (Join-Path $RepoRoot "aps_midi_prep_tool_app\helpers\windows_format_helper.py")
