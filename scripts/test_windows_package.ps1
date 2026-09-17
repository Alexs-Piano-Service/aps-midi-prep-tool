# Run the actual packaged EXE with no development tools on PATH. PowerShell 5.1+.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [ValidateRange(10, 1800)][int]$TimeoutSeconds = 240,
    [string]$ExpectedVersion = ''
)

$ErrorActionPreference = 'Stop'

function Stop-PackageProcessTree([int]$ProcessId) {
    $killer = New-Object System.Diagnostics.Process
    $killer.StartInfo.FileName = "$env:SystemRoot\System32\taskkill.exe"
    $killer.StartInfo.Arguments = "/PID $ProcessId /T /F"
    $killer.StartInfo.UseShellExecute = $false
    $killer.StartInfo.CreateNoWindow = $true
    try {
        if (!$killer.Start()) { throw 'Could not start process-tree termination.' }
        if (!$killer.WaitForExit(10000)) {
            $killer.Kill()
            $killer.WaitForExit(2000) | Out-Null
            throw 'Process-tree termination exceeded its own deadline.'
        }
    } finally {
        $killer.Dispose()
    }
}

$exe = (Resolve-Path -LiteralPath $ExecutablePath).Path
if ([IO.Path]::GetExtension($exe) -ne '.exe') { throw 'Select the final packaged application EXE.' }
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw "Choose a new results directory: $output" }
$expectedHash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
$working = Join-Path ([IO.Path]::GetTempPath()) ('aps-package-smoke-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $working | Out-Null
$process = New-Object System.Diagnostics.Process
$process.StartInfo.FileName = $exe
# Windows filenames cannot contain a quote; the full output directory has no trailing slash.
$process.StartInfo.Arguments = '--aps-package-smoke "' + $output.TrimEnd('\') + '"'
$process.StartInfo.WorkingDirectory = $working
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
$process.StartInfo.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
foreach ($key in @($process.StartInfo.EnvironmentVariables.Keys)) {
    if ($key -like 'APS_*' -or $key -in @(
        'PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
        'QT_QPA_PLATFORM', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH'
    )) {
        $process.StartInfo.EnvironmentVariables.Remove($key)
    }
}

$started = $false
try {
    $started = $process.Start()
    if (!$started) { throw 'Could not start the packaged application.' }
    # Drain both pipes concurrently so an error from a bundled helper cannot
    # block the application's exit or the deadline check.
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $finished = $process.WaitForExit($TimeoutSeconds * 1000)
    if (!$finished) {
        Stop-PackageProcessTree $process.Id
        if (!$process.WaitForExit(10000)) { throw 'The timed-out package process did not terminate.' }
    }
    if (!(Test-Path -LiteralPath $output)) { New-Item -ItemType Directory -Path $output -Force | Out-Null }
    if (!$stdout.Wait(5000) -or !$stderr.Wait(5000)) {
        throw 'The package process left an output pipe open after exiting.'
    }
    $stdout.Result | Set-Content -LiteralPath (Join-Path $output 'stdout.log') -Encoding UTF8
    $stderr.Result | Set-Content -LiteralPath (Join-Path $output 'stderr.log') -Encoding UTF8
    if (!$finished) { throw "The packaged application exceeded the $TimeoutSeconds second deadline." }
    if ($process.ExitCode -ne 0) {
        throw "Packaged application smoke checks failed with exit code $($process.ExitCode). See $output."
    }
    $reportPath = Join-Path $output 'package-smoke.json'
    if (!(Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw 'The EXE did not produce a package smoke report.' }
    $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.schema_version -ne 1 -or $report.frozen -ne $true -or $report.platform -ne 'win32') {
        throw 'The smoke report was not produced by the frozen Windows application.'
    }
    if ($report.executable_sha256 -ne $expectedHash -or
        (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'The smoke report does not match the final EXE checksum.'
    }
    if (!$report.app_version -or ($ExpectedVersion -and $report.app_version -ne $ExpectedVersion)) {
        throw 'The smoke report application version does not match the expected release.'
    }
    if ($report.status -ne 'passed') { throw 'The package smoke report does not show a complete pass.' }
    foreach ($name in @('ui', 'img', 'hfe', 'mp3')) {
        if ($report.cases.$name.status -ne 'passed') { throw "Package smoke case did not pass: $name" }
    }
    Write-Host "Final Windows EXE passed UI, IMG, HFE and MP3 checks: $reportPath"
} finally {
    if ($started -and !$process.HasExited) {
        Stop-PackageProcessTree $process.Id
        $process.WaitForExit(10000) | Out-Null
    }
    $process.Dispose()
    Remove-Item -LiteralPath $working -Recurse -Force
}
