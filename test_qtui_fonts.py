"""Khmer (and other non-Latin) names must be drawn at the normal text size.

Segoe UI has no Khmer letters; without a Khmer font in the list, Qt borrowed them from a font that
draws Khmer at half the size (10 px tall instead of 15 px), so Khmer folder names were tiny.
Run:  python -m unittest test_qtui_fonts -v
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui import style


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class FontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_khmer_fonts_follow_segoe_ui(self):
        self.assertEqual(style.FONT_FAMILIES[0], "Segoe UI")
        self.assertIn("Khmer UI", style.FONT_FAMILIES)          # ships with Windows
        self.assertIn("Leelawadee UI", style.FONT_FAMILIES)     # Windows' other font with Khmer

    def test_stylesheet_and_app_font_use_the_list(self):
        self.assertIn('font-family: "Segoe UI", "Khmer UI", "Leelawadee UI";', style.stylesheet("light"))
        style.apply(self.qapp, "Light")
        self.assertEqual(self.qapp.font().families()[:3], style.FONT_FAMILIES)


if __name__ == "__main__":
    unittest.main()
