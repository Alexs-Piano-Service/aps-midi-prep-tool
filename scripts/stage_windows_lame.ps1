# Stage the same MSYS2 encoder and runtime for native CI and release packaging.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceDirectory,
    [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = 'Stop'
$SourceDirectory = (Resolve-Path -LiteralPath $SourceDirectory).Path
if (Test-Path -LiteralPath $Destination) { throw "Choose a new staging directory: $Destination" }
$stage = New-Item -ItemType Directory -Path $Destination
# LAME and its libiconv dependency are installed by pacman from signed packages.
foreach ($name in 'lame.exe', 'libmp3lame-0.dll', 'libiconv-2.dll', 'libcharset-1.dll') {
    $source = Join-Path $SourceDirectory $name
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing LAME runtime file: $source" }
    Copy-Item -LiteralPath $source -Destination $stage.FullName
}
$prefix = Split-Path -Parent $SourceDirectory
$licenses = New-Item -ItemType Directory -Path (Join-Path $stage.FullName 'licenses')
Copy-Item -LiteralPath (Join-Path $prefix 'share/licenses/libiconv') -Destination $licenses.FullName -Recurse
Copy-Item -LiteralPath (Join-Path $prefix 'share/doc/lame') -Destination (Join-Path $stage.FullName 'doc') -Recurse

& (Join-Path $stage.FullName 'lame.exe') --version
if ($LASTEXITCODE -ne 0) { throw "Staged LAME failed to start (exit $LASTEXITCODE)." }
if ($env:GITHUB_PATH) { Add-Content -LiteralPath $env:GITHUB_PATH -Value $stage.FullName }
if ($env:GITHUB_ENV) { Add-Content -LiteralPath $env:GITHUB_ENV -Value "APS_WINDOWS_LAME_DIR=$($stage.FullName)" }
Write-Output $stage.FullName
