<#
  Builds dist\DriveSync.exe - a single file that runs without Python installed.
  Used by CI (.github/workflows/ci.yml) and works locally too:
    uv run powershell -ExecutionPolicy Bypass -File build.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name DriveSync `
    --icon assets\drivesync.ico `
    --add-data "README.md;." `
    --add-data "assets;assets" `
    --collect-data customtkinter `
    --hidden-import pystray._win32 `
    --hidden-import watchdog.observers.read_directory_changes `
    --collect-submodules send2trash `
    tray_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Get-Item dist\DriveSync.exe
Write-Host ("Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB))
