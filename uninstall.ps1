<#
  Removes Drive Sync from startup and stops it.
  Your files on Google Drive are not touched.
    powershell -ExecutionPolicy Bypass -File uninstall.ps1              # keep settings + login
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -RemoveFiles # also delete %LOCALAPPDATA%\DriveSync
#>
param([switch]$RemoveFiles)
$dest = Join-Path $env:LOCALAPPDATA "DriveSync"

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*tray_app.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped Drive Sync" }

$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "Drive Sync.lnk"
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed from startup" }

if ($RemoveFiles -and (Test-Path $dest)) {
    Remove-Item -Recurse -Force $dest
    Write-Host "Deleted $dest"
}
