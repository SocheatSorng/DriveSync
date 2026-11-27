<#
  Builds Drive Sync without needing Python installed: dist\DriveSync\ (one folder, what Setup installs),
  dist\DriveSync.exe (one portable file) and dist\DriveSync-Setup.exe.
  Used by CI (.github/workflows/ci.yml) and works locally too:
    uv run powershell -ExecutionPolicy Bypass -File build.ps1
#>
$ErrorActionPreference = "Stop"

# SHA-256 as lowercase hex, with .NET only. Not Get-FileHash: on CI this script runs in Windows PowerShell
# started from PowerShell 7, and Get-FileHash isn't found there, which failed every CI build.
function Get-Sha256Hex([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace "-", "").ToLower() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
Set-Location $PSScriptRoot

# Use the project's .venv when there is one: a system Python may have PyInstaller but not the app's
# packages, and PyInstaller then builds an .exe that crashes at start ("No module named ...").
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python -c "import watchdog, pystray, customtkinter, PySide6.QtWidgets, send2trash, PIL, requests, googleapiclient, google_auth_oauthlib, PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "$python is missing packages the app needs. Run 'uv sync' (or pip install -r requirements.txt pyinstaller) first."
}

# DriveSync.spec says what goes in (and leaves out Qt parts and Google API documents the app doesn't use).
# Two builds of the same app:
#   dist\DriveSync\      one folder - what Setup installs: starts in ~0.7 s (a single file unpacks itself
#                         to a temp folder on every start: 3.5-5 s), and Qt's DLLs stay replaceable (LGPL)
#   dist\DriveSync.exe   one file  - the portable copy in releases, for people who don't install
$env:DRIVESYNC_ONEDIR = "1"
& $python -m PyInstaller --noconfirm --clean --workpath build\onedir DriveSync.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (one-folder build)" }
$env:DRIVESYNC_ONEDIR = $null
& $python -m PyInstaller --noconfirm --clean --workpath build\onefile DriveSync.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (one-file build)" }

$folder = Get-ChildItem dist\DriveSync -Recurse -File
Write-Host ("Built {0} ({1:N1} MB in {2} files)" -f (Resolve-Path dist\DriveSync), (($folder | Measure-Object Length -Sum).Sum / 1MB), $folder.Count)
$exe = Get-Item dist\DriveSync.exe
Write-Host ("Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB))

# --- Installer (Inno Setup 6): dist\DriveSync-Setup.exe + its SHA-256 (the app checks it before updating)
$version = (& $python -c "from version import __version__; print(__version__)").Trim()
$iscc = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found - skipped the installer (DriveSync.exe is still built)."
    return
}
& $iscc /Q "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$setup = Get-Item dist\DriveSync-Setup.exe
$hash = Get-Sha256Hex $setup.FullName
Set-Content -Path "dist\DriveSync-Setup.exe.sha256" -Value "$hash  DriveSync-Setup.exe" -Encoding ascii -NoNewline
Write-Host ("Built {0} ({1:N1} MB), version {2}" -f $setup.FullName, ($setup.Length / 1MB), $version)
