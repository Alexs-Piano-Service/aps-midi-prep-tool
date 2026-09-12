# Fetch the same standalone HFE helper for CI integration and release packaging.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Destination)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Destination) { throw "Choose a new staging directory: $Destination" }
$stage = New-Item -ItemType Directory -Path $Destination
$archive = Join-Path $stage.FullName 'greaseweazle-1.23-win64.zip'
$url = 'https://github.com/keirf/greaseweazle/releases/download/v1.23/greaseweazle-1.23-win64.zip'
# SHA-256 published on the upstream GitHub release asset.
$sha256 = 'f409ae2411506eacecd60915c361c7bfa7293795e3e488fad4927ddd7d146464'
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $sha256) {
    throw 'Greaseweazle archive checksum mismatch.'
}
Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $stage.FullName 'extracted')
$executables = @(Get-ChildItem -LiteralPath (Join-Path $stage.FullName 'extracted') -Recurse -Filter 'gw.exe')
if ($executables.Count -ne 1) { throw 'Expected exactly one standalone gw.exe in the verified archive.' }
$toolDirectory = $executables[0].DirectoryName
if ($env:GITHUB_PATH) { Add-Content -LiteralPath $env:GITHUB_PATH -Value $toolDirectory }
if ($env:GITHUB_ENV) { Add-Content -LiteralPath $env:GITHUB_ENV -Value "APS_WINDOWS_GW_DIR=$toolDirectory" }
Write-Output $toolDirectory
