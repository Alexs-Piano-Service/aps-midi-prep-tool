# Exercise the actual installer payload and bind its evidence to both assets.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$StandalonePath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$ExpectedVersion = ''
)
$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$standalone = (Resolve-Path -LiteralPath $StandalonePath).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'Choose a new installer test results directory.' }
New-Item -ItemType Directory -Path $output | Out-Null
$install = Join-Path $output 'installed application'
$log = Join-Path $output 'install.log'
$arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER',
    ('/DIR="' + $install + '"'), ('/LOG="' + $log + '"'))
$process = Start-Process -FilePath $installer -ArgumentList $arguments -PassThru
try {
    if (!$process.WaitForExit(120000)) { $process.Kill(); throw 'Installer exceeded two minutes.' }
    if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
} finally { $process.Dispose() }
$exe = Join-Path $install 'APS MIDI Prep Tool.exe'
$expectedHash = (Get-FileHash -LiteralPath $standalone -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'The installed payload differs from the tested standalone EXE.'
}
& (Join-Path $PSScriptRoot 'test_windows_package.ps1') -ExecutablePath $exe `
    -OutputDirectory (Join-Path $output 'smoke') -ExpectedVersion $ExpectedVersion -WorkingDirectory $install
@{
    installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    installed_executable_sha256 = $expectedHash
    standalone_sha256 = $expectedHash
    status = 'passed'
    clean_machine = $false
    standard_user_program_files_verified = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'installer-acceptance.json') -Encoding UTF8
