"""Tests for what the PyInstaller build keeps and leaves out (build_filter.py, used by DriveSync.spec).
Run:  python -m unittest test_build_filter -v
"""
import hashlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import build_filter
from build_filter import keep


class KeepTests(unittest.TestCase):
    def test_keeps_what_the_window_needs(self):
        for dest in ("PySide6/Qt6Core.dll", "PySide6/Qt6Gui.dll", "PySide6/Qt6Widgets.dll",
                     "PySide6/QtWidgets.pyd", "PySide6\\plugins\\platforms\\qwindows.dll",
                     "PySide6/plugins/styles/qmodernwindowsstyle.dll", "PySide6/plugins/imageformats/qico.dll",
                     "googleapiclient/discovery_cache/documents/drive.v3.json", "customtkinter/assets/x.json",
                     "licenses/LGPL-3.0.txt", "python314.dll"):
            self.assertTrue(keep(dest), dest)

    def test_leaves_out_what_it_doesnt_use(self):
        for dest in ("PySide6/opengl32sw.dll", "PySide6/translations/qtbase_de.qm", "PySide6/Qt6Network.dll",
                     "PySide6/QtNetwork.pyd", "PySide6/Qt6Qml.dll", "PySide6/Qt6Svg.dll",
                     "PySide6/plugins/tls/qschannelbackend.dll", "PySide6/plugins/imageformats/qjpeg.dll",
                     "PySide6/plugins/platforms/qoffscreen.dll",
                     "googleapiclient/discovery_cache/documents/sheets.v4.json"):
            self.assertFalse(keep(dest), dest)

    def test_kept_qt_plugins_exist_in_this_pyside6(self):
        """A plugin renamed by a PySide6 update would be left out and the window couldn't start."""
        try:
            import PySide6
        except ImportError:
            self.skipTest("PySide6 not installed")
        plugins = Path(PySide6.__file__).parent / "plugins"
        for rel in build_filter.QT_PLUGINS_KEPT:
            self.assertTrue((plugins / rel).is_file(), rel)

    def test_drive_discovery_document_exists(self):
        import googleapiclient
        docs = Path(googleapiclient.__file__).parent / "discovery_cache" / "documents"
        self.assertTrue((docs / build_filter.GOOGLE_DOC_KEPT).is_file())

    def test_licence_texts_are_shipped(self):
        root = Path(__file__).parent / "licenses"
        for name in ("LGPL-3.0.txt", "GPL-3.0.txt", "THIRD-PARTY-NOTICES.txt"):
            self.assertTrue((root / name).is_file(), name)
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", (root / "LGPL-3.0.txt").read_text(encoding="utf-8"))


class VersionInfoTests(unittest.TestCase):
    """DriveSync.exe carries Windows version details: without them Defender's machine-learning check
    flagged the one-folder launcher as "Trojan:Win32/Bearfoos.A!ml" and deleted it on install."""

    def test_version_details(self):
        from version import __version__
        info = build_filter.version_info(__version__)
        text = str(info)
        for key, value in (("ProductName", "Drive Sync"), ("FileVersion", __version__),
                           ("OriginalFilename", "DriveSync.exe"), ("CompanyName", "Drive Sync")):
            self.assertIn(f"'{key}', '{value}'", text)
        nums = tuple(int(x) for x in __version__.split("."))
        self.assertEqual(info.ffi.fileVersionMS >> 16, nums[0])

    def test_spec_uses_it(self):
        spec = (Path(__file__).parent / "DriveSync.spec").read_text(encoding="utf-8")
        self.assertIn("version=version_info(__version__)", spec)


@unittest.skipUnless(sys.platform == "win32", "build.ps1 runs on Windows")
class SetupChecksumTests(unittest.TestCase):
    """build.ps1 writes DriveSync-Setup.exe.sha256, which the updater checks before installing."""

    def test_checksum_matches_and_needs_no_get_filehash(self):
        # On CI, build.ps1 runs in Windows PowerShell started from PowerShell 7, and Get-FileHash wasn't
        # found there ("not recognized"), so every CI build failed after building the exes. Plain .NET
        # hashing needs no PowerShell module. Python's hashlib checks the result.
        script = (Path(__file__).parent / "build.ps1").read_text(encoding="utf-8")
        code = [line.split("#", 1)[0] for line in script.splitlines()]   # comments may mention it
        self.assertFalse([line for line in code if "Get-FileHash" in line])
        func = re.search(r"^function Get-Sha256Hex\b.*?^}", script, re.M | re.S)
        self.assertIsNotNone(func, "build.ps1 defines Get-Sha256Hex")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "setup.bin"
            f.write_bytes(b"Drive Sync" * 1000)
            out = subprocess.run(  # noqa: S603 - a fixed command on a test file
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",  # noqa: S607 - Windows PowerShell
                 f"$ErrorActionPreference = 'Stop'; {func.group(0)}; Get-Sha256Hex '{f}'"],
                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), hashlib.sha256(b"Drive Sync" * 1000).hexdigest())

if __name__ == "__main__":
    unittest.main()
