# Installs a native Windows onedir build per user; keeps previous app and all data.
param(
    [string]$Source = "$PSScriptRoot\CodeAtlas",
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Code Atlas"
)
$ErrorActionPreference = 'Stop'
if (!(Test-Path "$Source\CodeAtlas.exe")) { throw 'Native Windows build not found' }
if (!(Test-Path "$Source\CodeAtlasDesktop.exe")) { throw 'Native desktop launcher not found' }
# Existing data must not be moved with a replaced application directory.
$DataDirectory = if ($env:ATLAS_DATA_DIR) { $env:ATLAS_DATA_DIR } else { "$env:LOCALAPPDATA\Code Atlas" }
if (Test-Path $DataDirectory -PathType Container) {
    $CanonicalData = (Resolve-Path $DataDirectory).Path.TrimEnd('\') + '\'
    foreach ($ApplicationDirectory in @($Source, $Destination)) {
        if (Test-Path $ApplicationDirectory -PathType Container) {
            $CanonicalApp = (Resolve-Path $ApplicationDirectory).Path.TrimEnd('\') + '\'
            if ($CanonicalData.StartsWith($CanonicalApp, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'Move user data outside the application directory before updating.'
            }
        }
    }
}
$Staging = "$Destination.install-$PID"
$Backup = "$Destination.previous-$(Get-Date -Format yyyyMMdd-HHmmss)-$PID"
New-Item -ItemType Directory -Force (Split-Path $Destination) | Out-Null
try {
    Copy-Item -Recurse $Source $Staging
    & "$Staging\CodeAtlas.exe" --version
    if ($LASTEXITCODE -ne 0) { throw 'New application did not pass --version' }
    # Compatible updates inspect the existing directory without copying its contents.
    & "$Staging\CodeAtlas.exe" --data-dir $DataDirectory --prepare-update
    if ($LASTEXITCODE -ne 0) { throw 'Stop Atlas before update; data compatibility check did not complete' }
    if (Test-Path $Destination) { Move-Item $Destination $Backup }
    try { Move-Item $Staging $Destination }
    catch { if (Test-Path $Backup) { Move-Item $Backup $Destination }; throw }
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Code Atlas.lnk")
    $Shortcut.TargetPath = "$Destination\CodeAtlasDesktop.exe"
    $Shortcut.Arguments = '--desktop'
    $Shortcut.WorkingDirectory = $Destination
    $Shortcut.Save()
    Write-Output "Installed: $Destination. User data is preserved in $env:LOCALAPPDATA\Code Atlas."
} finally {
    if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
}
