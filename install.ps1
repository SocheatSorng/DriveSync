<#
  Installs Drive Sync so it starts automatically when you log in to Windows.

  Usage (from this folder):
    powershell -ExecutionPolicy Bypass -File install.ps1 -Folder "D:\Photos"
    powershell -ExecutionPolicy Bypass -File install.ps1 -Folder "D:\Photos","D:\Work" -IntervalMinutes 10

  What it does:
    1. Copies the app to %LOCALAPPDATA%\DriveSync (keeps an existing config/login there)
    2. Installs the Python packages it needs
    3. Writes config.json with your folder(s) (only if -Folder is given)
    4. Adds a shortcut to your Startup folder (runs with pythonw = no console window)
    5. Starts it now - look for the blue arrow icon in the system tray
#>
param(
    [string[]]$Folder,
    [string]$DriveParent = "PC Backup",
    [int]$IntervalMinutes = 15,
    [switch]$Delete
)
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "DriveSync"

# --- find Python -----------------------------------------------------------
$python = (& python -c "import sys; print(sys.executable)").Trim()
if (-not $python -or -not (Test-Path $python)) { throw "Python not found. Install Python 3 first." }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }
Write-Host "Using Python: $python"

# --- stop a running copy so files can be replaced --------------------------
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*tray_app.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped running Drive Sync" }

# --- copy files ------------------------------------------------------------
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($f in "drive_sync.py", "tray_app.py", "gui.py", "accounts.py", "browsers.py", "watcher.py", "activity.py", "activity_view.py", "twoway.py", "progress.py", "requirements.txt", "README.md", "uninstall.ps1") {
    Copy-Item (Join-Path $src $f) $dest -Force
}
Copy-Item (Join-Path $src "assets") $dest -Recurse -Force
foreach ($f in "credentials.json", "token.json") {   # copy login files only if not already there
    $from = Join-Path $src $f
    if ((Test-Path $from) -and -not (Test-Path (Join-Path $dest $f))) { Copy-Item $from $dest }
}
Write-Host "Installed to $dest"

# --- dependencies ----------------------------------------------------------
& $python -m pip install -q -r (Join-Path $dest "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- config ----------------------------------------------------------------
$configPath = Join-Path $dest "config.json"
if ($Folder) {
    $list = @()
    foreach ($f in $Folder) {
        $full = (Resolve-Path $f).Path
        $name = Split-Path $full -Leaf
        $list += [ordered]@{ local = $full; drive_folder = "$DriveParent/$name"; delete = [bool]$Delete; exclude = @() }
    }
    $cfg = [ordered]@{
        interval_minutes      = $IntervalMinutes
        startup_delay_seconds = 30
        notify_on_changes     = $false
        folders               = $list
    }
    $json = ConvertTo-Json -InputObject $cfg -Depth 5
    [IO.File]::WriteAllText($configPath, $json, (New-Object Text.UTF8Encoding $false))
    Write-Host "Wrote $configPath"
} elseif (-not (Test-Path $configPath)) {
    Write-Host "No -Folder given: add folders in the Drive Sync window."
}

# --- startup shortcut ------------------------------------------------------
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Drive Sync.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $pythonw
$sc.Arguments = "`"$(Join-Path $dest 'tray_app.py')`" --hidden"   # at login: tray only, no window
$sc.WorkingDirectory = $dest
$sc.Description = "Sync folders to Google Drive"
$sc.Save()
Write-Host "Added to startup: $lnk"

if (-not (Test-Path (Join-Path $dest "credentials.json"))) {
    Write-Host "Next: in the Drive Sync window open Settings > Import credentials.json, then Sign in."
}

# --- start now (with the window open) -----------------------------------------
Start-Process -FilePath $pythonw -ArgumentList "`"$(Join-Path $dest 'tray_app.py')`"" -WorkingDirectory $dest
Write-Host "Drive Sync is open. Closing the window keeps it running in the system tray."
