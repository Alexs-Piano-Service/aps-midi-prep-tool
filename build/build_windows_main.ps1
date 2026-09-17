param(
    [string]$PyInstaller = "pyinstaller",
    [string]$Python = "python",
    [string]$Name = "APS MIDI Prep Tool",
    [switch]$OneFile,
    [string]$GreaseweazleDirectory = $env:APS_WINDOWS_GW_DIR,
    [string]$LameDirectory = $env:APS_WINDOWS_LAME_DIR,
    [string]$LameExe = "",
    [bool]$BundleLame = $true
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot
& $Python scripts/release_metadata.py
if ($LASTEXITCODE -ne 0) { throw 'Release metadata validation failed.' }

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
    "--name", $Name,
    "--icon", (Join-Path $RepoRoot "aps_midi_prep_tool_app\aps.ico"),
    "--manifest", (Join-Path $RepoRoot "manifests\main_as_invoker.xml")
)
if ($OneFile) { $PyInstallerArgs += '--onefile' }

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

if (-not $GreaseweazleDirectory) {
    $stage = Join-Path ([IO.Path]::GetTempPath()) ('aps-hfe-' + [Guid]::NewGuid().ToString('N'))
    $GreaseweazleDirectory = & (Join-Path $RepoRoot 'scripts/stage_windows_hfe_tool.ps1') -Destination $stage
}
$GreaseweazleDirectory = (Resolve-Path -LiteralPath $GreaseweazleDirectory).Path
& $Python (Join-Path $RepoRoot 'scripts/verify_windows_hfe_stage.py') $GreaseweazleDirectory
if ($LASTEXITCODE -ne 0) { throw 'Standalone Greaseweazle staging validation failed.' }
# The standalone helper needs its complete library, DLLs, and licenses.
$PyInstallerArgs += @('--add-data', "$GreaseweazleDirectory;aps_midi_prep_tool_app/bin/greaseweazle")

if ($BundleLame) {
    if (-not $LameDirectory) {
        $ResolvedLame = Resolve-Executable -ExplicitPath $LameExe -CommandName "lame.exe"
        if (-not $ResolvedLame) { throw 'Install the MSYS2 LAME package or provide a staged LameDirectory.' }
        $stage = Join-Path ([IO.Path]::GetTempPath()) ('aps-lame-' + [Guid]::NewGuid().ToString('N'))
        # The staging script's encoder version output is diagnostic, not a path.
        & (Join-Path $RepoRoot 'scripts/stage_windows_lame.ps1') -SourceDirectory (Split-Path -Parent $ResolvedLame) -Destination $stage | Out-Host
        $LameDirectory = $stage
    }
    foreach ($file in 'lame.exe', 'libmp3lame-0.dll', 'libiconv-2.dll', 'libcharset-1.dll') {
        if (!(Test-Path -LiteralPath (Join-Path $LameDirectory $file) -PathType Leaf)) { throw "Missing staged LAME runtime: $file" }
    }
    $PyInstallerArgs += @('--add-data', "$LameDirectory;bin")
}

& $PyInstaller @PyInstallerArgs (Join-Path $RepoRoot "aps_midi_prep_tool.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
