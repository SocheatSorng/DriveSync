"""Tests for what the PyInstaller build keeps and leaves out (build_filter.py, used by DriveSync.spec).
Run:  python -m unittest test_build_filter -v
"""
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


if __name__ == "__main__":
    unittest.main()
