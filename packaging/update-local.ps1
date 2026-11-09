# Repository update entrypoint; ready native builds do not require external Python.
[CmdletBinding()]
param(
    [switch]$Build,
    [string]$Python = $env:ATLAS_BUILD_PYTHON,
    [string]$Target = $env:ATLAS_BUILD_TARGET,
    [string]$Source,
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Code Atlas",
    [string]$DataDirectory = $(if ($env:ATLAS_DATA_DIR) { $env:ATLAS_DATA_DIR } else { "$env:LOCALAPPDATA\Code Atlas" }),
    [switch]$NoStart,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use update-local.sh on macOS/Linux.' }
$Root = Split-Path $PSScriptRoot
if ($Build -and $Source) { throw '-Build cannot use -Source' }
if (!$Source) { $Source = Join-Path $Root 'dist\CodeAtlas' }
$Source = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Source)
$Destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
$DataDirectory = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DataDirectory)
if ($Python -and (Test-Path -LiteralPath $Python -PathType Leaf)) { $Python = (Resolve-Path -LiteralPath $Python).Path }
# The shell itself can be emulated. Prefer the physical OS architecture by default.
$Machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if (!$Target) {
    $Target = switch ($Machine) { 'ARM64' { 'win32-arm64' }; 'AMD64' { 'win32-x86_64' }; default { throw "Unsupported CPU: $Machine" } }
}
$Target = $Target.ToLowerInvariant().Replace('windows-', 'win32-').Replace('-amd64', '-x86_64').Replace('-x64', '-x86_64').Replace('-aarch64', '-arm64')
if ($Target -notin @('win32-arm64', 'win32-x86_64')) { throw 'Target must be windows-arm64 or windows-x64' }

function Assert-SeparatePaths([string]$Left, [string]$Right) {
    # Canonical lexical paths include future destinations. Reparse-point ancestors are refused below.
    $Left = $Left.TrimEnd('\') + '\'
    $Right = $Right.TrimEnd('\') + '\'
    if ($Left.StartsWith($Right, [StringComparison]::OrdinalIgnoreCase) -or $Right.StartsWith($Left, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Paths must be separate: $Left and $Right"
    }
}

foreach ($Directory in @($Source, $Destination, $DataDirectory)) {
    $Ancestor = $Directory
    while ($Ancestor) {
        if (Test-Path -LiteralPath $Ancestor) {
            if ((Get-Item -LiteralPath $Ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Choose a path without reparse-point ancestors: $Ancestor"
            }
        }
        $Ancestor = Split-Path $Ancestor
    }
}
Assert-SeparatePaths $Source $Destination
Assert-SeparatePaths $Source $DataDirectory
Assert-SeparatePaths $Destination $DataDirectory
Write-Output "Repository: $Root`nTarget: $Target`nSource: $Source`nDestination: $Destination`nData (kept in place): $DataDirectory`nBuild: $Build; start: $(!$NoStart)"
if ($DryRun) { return }

function Test-BuildPython([string]$Candidate) {
    # Probe process ABI, including an x64 Python running on an ARM Windows host.
    if (!(Get-Command $Candidate -ErrorAction SilentlyContinue)) { return $false }
    try {
        $null = & $Candidate -c 'import sys; sys.path.insert(0, "packaging"); from build_target import select_target; select_target(sys.argv[1])' $Target 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Assert-NativeExecutable([string]$Path) {
    # Read the PE machine field rather than trusting filenames or a build manifest.
    $Stream = [IO.File]::OpenRead($Path)
    $Reader = New-Object IO.BinaryReader($Stream)
    try {
        if ($Reader.ReadUInt16() -ne 0x5a4d) { throw "Not a PE executable: $Path" }
        $Stream.Position = 0x3c
        $Offset = $Reader.ReadUInt32()
        $Stream.Position = $Offset
        if ($Reader.ReadUInt32() -ne 0x00004550) { throw "Invalid PE signature: $Path" }
        $Expected = if ($Target -eq 'win32-arm64') { 0xaa64 } else { 0x8664 }
        if ($Reader.ReadUInt16() -ne $Expected) { throw "Executable does not match ${Target}: $Path" }
    } finally { $Reader.Dispose() }
}

$PreviousData = $env:ATLAS_DATA_DIR
$PreviousPythonHome = $env:PYTHONHOME
$PreviousPythonPath = $env:PYTHONPATH
$PreviousPath = $env:PATH
Push-Location $Root
try {
    if ($Build) {
        if ($Python) {
            if (!(Test-BuildPython $Python)) { throw "Python does not match ${Target}: $Python" }
        } else {
            foreach ($Candidate in @("$Root\.venv\Scripts\python.exe", 'python')) {
                if (Test-BuildPython $Candidate) { $Python = $Candidate; break }
            }
            if (!$Python) { throw "No $Target Python found. See packaging/UPDATING.md; use -Python PATH." }
        }
        & $Python -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Build environment dependencies are inconsistent' }
        & $Python packaging/build.py --target $Target
        if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
    }
    $Binary = Join-Path $Source 'CodeAtlas.exe'
    Assert-NativeExecutable $Binary
    Assert-NativeExecutable (Join-Path $Source 'CodeAtlasDesktop.exe')
    $env:ATLAS_DATA_DIR = $DataDirectory
    & $Binary --data-dir $DataDirectory --prepare-update
    if ($LASTEXITCODE -ne 0) { throw 'Close Atlas and retry; data schema may require explicit migration.' }
    # Frozen validation must succeed without discovering a developer Python on PATH.
    try {
        $env:PYTHONHOME = $null
        $env:PYTHONPATH = $null
        $env:PATH = ''
        & $Binary --native-document-check
        if ($LASTEXITCODE -ne 0) { throw 'Frozen document validation failed' }
    } finally {
        $env:PATH = $PreviousPath
        $env:PYTHONHOME = $PreviousPythonHome
        $env:PYTHONPATH = $PreviousPythonPath
    }
    & "$PSScriptRoot\install-windows.ps1" -Source $Source -Destination $Destination
    if ($NoStart -ne $true) {
        Start-Process (Join-Path $Destination 'CodeAtlasDesktop.exe') -ArgumentList @('--desktop', '--data-dir', ('"' + $DataDirectory + '"'))
    }
    Write-Output 'Update complete. Previous application retained; data was not copied or archived.'
} finally {
    $env:ATLAS_DATA_DIR = $PreviousData
    Pop-Location
}
