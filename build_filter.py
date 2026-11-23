"""What the PyInstaller build leaves out (used by DriveSync.spec, tested by test_build_filter.py).

PyInstaller's Qt hooks bundle more than a Qt Widgets window needs; this trims ~30 MB from the build.
"""
from __future__ import annotations

from pathlib import PurePath

# Qt modules the window doesn't use (it needs QtCore, QtGui and QtWidgets only).
QT_UNUSED = ["QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtOpenGL", "QtOpenGLWidgets", "QtPdf",
             "QtPdfWidgets", "QtSvg", "QtSvgWidgets", "QtPrintSupport", "QtSql", "QtTest", "QtDBus", "QtXml",
             "QtConcurrent", "QtDesigner", "QtHelp", "QtUiTools", "QtMultimedia", "QtWebEngineCore"]
# Qt plugins it does need: drawing on Windows, the Windows 11 style, and reading the .ico window icon.
QT_PLUGINS_KEPT = {"platforms/qwindows.dll", "styles/qmodernwindowsstyle.dll", "imageformats/qico.dll"}
# The only Google API the app uses; its discovery document is read offline when connecting.
GOOGLE_DOC_KEPT = "drive.v3.json"


def keep(dest: str) -> bool:
    """Whether a file PyInstaller collected (its path inside the build) goes into the app."""
    p = PurePath(dest.replace("\\", "/")).as_posix()
    if p.startswith("PySide6/"):
        rel = p[len("PySide6/"):]
        if rel.startswith("translations/"):          # the app is in English: no Qt translations
            return False
        if rel == "opengl32sw.dll":                   # software OpenGL fallback: a widgets app never uses it
            return False
        if rel.startswith("plugins/"):
            return rel[len("plugins/"):] in QT_PLUGINS_KEPT
        stem = rel.split(".")[0]
        if any(stem in (m, f"Qt6{m[2:]}") for m in QT_UNUSED):
            return False
    if p.startswith("googleapiclient/discovery_cache/documents/"):
        return p.endswith("/" + GOOGLE_DOC_KEPT)
    return True


def version_info(version: str):
    """Windows version details for DriveSync.exe (Properties → Details): product, company, description
    and version. A PyInstaller exe has none by default, which antivirus heuristics count against it
    (Defender flagged the one-folder launcher as "Trojan:Win32/Bearfoos.A!ml")."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )
    nums = tuple(int(x) for x in (version.split(".") + ["0", "0", "0"])[:4])
    strings = [
        StringStruct("CompanyName", "Drive Sync"),
        StringStruct("FileDescription", "Drive Sync - keeps folders in sync with Google Drive"),
        StringStruct("FileVersion", version),
        StringStruct("InternalName", "DriveSync"),
        StringStruct("LegalCopyright", "Copyright (c) Drive Sync contributors"),
        StringStruct("OriginalFilename", "DriveSync.exe"),
        StringStruct("ProductName", "Drive Sync"),
        StringStruct("ProductVersion", version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", strings)]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])],
    )
