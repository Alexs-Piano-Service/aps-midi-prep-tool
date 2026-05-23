param(
    [string]$PyInstaller = "pyinstaller",
    [string]$LameExe = "",
    [bool]$BundleLame = $true
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Resolve-Executable {
    param(
        [string]$ExplicitPath,
        [string]$CommandName
    )

    if ($ExplicitPath) {
        $ResolvedPath = Resolve-Path $ExplicitPath -ErrorAction Stop
        return $ResolvedPath.Path
    }

    $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    return ""
}

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

if ($BundleLame) {
    $ResolvedLame = Resolve-Executable -ExplicitPath $LameExe -CommandName "lame.exe"
    if (-not $ResolvedLame) {
        throw "LAME is required for the Windows bundle. Install LAME or pass -BundleLame `$false."
    }
    $PyInstallerArgs += @(
        "--add-binary",
        "$ResolvedLame;bin"
    )
}

& $PyInstaller @PyInstallerArgs (Join-Path $RepoRoot "aps_midi_prep_tool.py")
