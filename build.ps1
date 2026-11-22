<#
  Builds dist\DriveSync.exe - a single file that runs without Python installed.
  Used by CI (.github/workflows/ci.yml) and works locally too:
    uv run powershell -ExecutionPolicy Bypass -File build.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Use the project's .venv when there is one: a system Python may have PyInstaller but not the app's
# packages, and PyInstaller then builds an .exe that crashes at start ("No module named ...").
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python -c "import watchdog, pystray, customtkinter, send2trash, PIL, requests, googleapiclient, google_auth_oauthlib, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "$python is missing packages the app needs. Run 'uv sync' (or pip install -r requirements.txt pyinstaller) first."
}

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
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
