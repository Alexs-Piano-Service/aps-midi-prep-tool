# Requires only Windows PowerShell 5.1. Run from an extracted Windows test kit.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppExe,
    [string]$ResultsDirectory = (Join-Path $PSScriptRoot ('results-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [switch]$RequireCleanMachine,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath $AppExe).Path
if ([IO.Path]::GetExtension($exe) -ne '.exe') { throw 'Select the packaged application EXE.' }
$manifestPath = Join-Path $PSScriptRoot 'manifest.json'
if (!(Test-Path -LiteralPath $manifestPath)) { throw 'Run this script from an extracted windows-test-kit.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($entry in $manifest.fixtures.PSObject.Properties) {
    $fixture = Join-Path $PSScriptRoot $entry.Name
    if (!(Test-Path -LiteralPath $fixture -PathType Leaf)) { throw "Missing fixture: $fixture" }
    if ((Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash -ne $entry.Value.sha256) {
        throw "Fixture checksum failed: $fixture. Extract a fresh test kit."
    }
}

$commands = @('mformat', 'mcopy', 'mdir', 'mdel', 'mren', 'gw', 'python', 'git')
$found = @{}
foreach ($name in $commands) {
    $found[$name] = @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source)
}
$imageToolsFound = @('mformat', 'mcopy', 'mdir', 'mdel', 'mren', 'gw') | Where-Object { $found[$_].Count -gt 0 }
if ($RequireCleanMachine -and @($imageToolsFound).Count -gt 0) {
    throw 'External image tools are on PATH. Use a clean Windows VM for release acceptance, or omit -RequireCleanMachine for a PATH-isolation smoke check.'
}
if (Test-Path -LiteralPath $ResultsDirectory) { throw "Choose a new results directory: $ResultsDirectory" }
$results = New-Item -ItemType Directory -Path $ResultsDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures') -Destination (Join-Path $results.FullName 'work') -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'results-template.csv') -Destination (Join-Path $results.FullName 'results.csv')

$record = [ordered]@{
    StartedUtc = [DateTime]::UtcNow.ToString('o')
    WindowsVersion = [Environment]::OSVersion.VersionString
    Is64BitOS = [Environment]::Is64BitOperatingSystem
    PowerShellVersion = $PSVersionTable.PSVersion.ToString()
    AppExe = $exe
    AppSHA256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
    SignatureStatus = (Get-AuthenticodeSignature -LiteralPath $exe).Status.ToString()
    FixtureVersion = $manifest.app_version
    OriginalPathTools = $found
    RequireCleanMachine = [bool]$RequireCleanMachine
    AcceptanceStatus = 'NOT RUN - complete results.csv and TEST-PLAN.md'
    Launched = $false
}
$originalPath = $env:PATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $record.RestrictedPath = $env:PATH
    foreach ($name in @('mformat', 'mcopy', 'mdir', 'mdel', 'mren', 'gw')) {
        if (Get-Command $name -CommandType Application -ErrorAction SilentlyContinue) {
            throw "External tool still visible on the restricted PATH: $name"
        }
    }
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $results.FullName 'environment.json') -Encoding UTF8
    Write-Host "Test copies and results: $($results.FullName)"
    Write-Host 'Follow TEST-PLAN.md and enter PASS / FAIL / N/A in results.csv. Preflight is not a test pass.'
    if ($Launch) {
        # The tester explicitly requested the visible application window.
        $app = Start-Process -FilePath $exe -WorkingDirectory $results.FullName -PassThru
        $record.Launched = $true
        $record.ProcessId = $app.Id
        $app.WaitForExit()
        $record.ExitCode = $app.ExitCode
    }
} finally {
    $env:PATH = $originalPath
    $record.FinishedUtc = [DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $results.FullName 'environment.json') -Encoding UTF8
}
if ($Launch -and $record.ExitCode -ne 0) { throw "Application exited with code $($record.ExitCode). See environment.json." }
