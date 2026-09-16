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
    "--collect-data", "certifi",
    "--name", "APS MIDI Prep Tool",
    "--icon", (Join-Path $RepoRoot "aps_midi_prep_tool_app\aps.ico"),
    "--manifest", (Join-Path $RepoRoot "manifests\main_as_invoker.xml")
)

# Image creation must work on a recipient's PC without a separate mtools install.
# PyInstaller also collects the DLL dependencies of these executables.
foreach ($ToolName in @("mformat", "mcopy", "mdir", "mdel", "mren")) {
    $BundledTool = Join-Path $RepoRoot "aps_midi_prep_tool_app\bin\mtools\$ToolName.exe"
    $ToolPath = if (Test-Path $BundledTool) { $BundledTool } else {
        Resolve-Executable -ExplicitPath "" -CommandName "$ToolName.exe"
    }
    if (-not $ToolPath) { throw "mtools is required for the Windows bundle: $ToolName.exe was not found." }
    $PyInstallerArgs += @("--add-binary", "$ToolPath;aps_midi_prep_tool_app\bin\mtools")
}

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
