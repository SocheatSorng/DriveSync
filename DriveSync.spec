# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build of Drive Sync (run by build.ps1). Kept in git: it trims what gets bundled.
#
#   one file (default):      dist\DriveSync.exe
#   one folder (DRIVESYNC_ONEDIR=1):  dist\DriveSync\DriveSync.exe + its DLLs
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, SPECPATH)  # noqa: F821 - set by PyInstaller
from build_filter import QT_UNUSED, keep  # noqa: E402 - what's left out of the build (tested)

ONEDIR = os.environ.get("DRIVESYNC_ONEDIR") == "1"

datas = [("README.md", "."), ("assets", "assets"), ("licenses", "licenses")]   # licenses: Qt is LGPL-3.0
datas += collect_data_files("customtkinter")
hiddenimports = ["pystray._win32", "watchdog.observers.read_directory_changes"]
hiddenimports += collect_submodules("send2trash")

a = Analysis(
    ["tray_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[f"PySide6.{m}" for m in QT_UNUSED],
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if keep(b[0])]
a.datas = [d for d in a.datas if keep(d[0])]
pyz = PYZ(a.pure)

common = dict(name="DriveSync", debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
              console=False, disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
              codesign_identity=None, entitlements_file=None, icon=["assets/drivesync.ico"])

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DriveSync")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], upx_exclude=[], runtime_tmpdir=None, **common)
