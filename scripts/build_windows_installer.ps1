[CmdletBinding()]
param(
    [string]$ExecutablePath = 'dist/APSMIDIPrepTool.exe',
    [string]$OutputDirectory = 'dist',
    [string]$Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& $Python (Join-Path $PSScriptRoot 'release_metadata.py')
if ($LASTEXITCODE -ne 0) { throw 'Release metadata validation failed.' }
$metadata = Get-Content (Join-Path $root 'packaging/release.json') -Raw | ConvertFrom-Json
$payload = (Resolve-Path -LiteralPath $ExecutablePath).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $output -Force | Out-Null
& $Compiler "/DAppVersion=$($metadata.version)" "/DPayload=$payload" "/O$output" (Join-Path $root 'packaging/windows_installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Windows installer compilation failed.' }
