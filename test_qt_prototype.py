"""Smoke test for the PySide6 prototype window (prototypes/qt_main_window.py).

PySide6 isn't one of the app's dependencies, so this is skipped in the normal test run. Run it with:
    python -m uv run --no-project --with pyside6 python -m unittest test_qt_prototype -v
"""
import os
import sys
import unittest
from pathlib import Path

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None

sys.path.insert(0, str(Path(__file__).parent / "prototypes"))


@unittest.skipIf(PySide6 is None, "PySide6 not installed (prototype only)")
class QtPrototypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import qt_main_window
        from PySide6.QtWidgets import QApplication
        cls.q = qt_main_window
        cls.app = QApplication.instance() or QApplication([])

    def test_builds_all_tabs_in_both_themes(self):
        for theme in ("light", "dark"):
            w = self.q.MainWindow(theme)
            self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                             ["Folders", "Settings", "Activity", "Space"])
            self.assertEqual(self.q.THEME["window"], self.q.THEMES[theme]["window"])
            w.close()

    def test_problem_card_starts_open_with_every_file(self):
        w = self.q.MainWindow("light")
        badges = [lb for lb in w.activity.findChildren(self.q.QLabel) if lb.property("role") == "badge"]
        self.assertEqual(len(badges), 200)                       # the 200-file run, open
        self.assertEqual(badges[0].text(), "Problem")            # problems listed first
        w.close()

    def test_filters(self):
        w = self.q.MainWindow("light")
        a = w.activity
        a._set_view("Problems")
        self.assertTrue(all(any(it["kind"] in ("error", "conflict") for it in e["items"]) for _i, e in a._shown()))
        a._set_view("All")
        a._set_query("img_0042")
        self.assertEqual([e["folder"] for _i, e in a._shown()], ["Image"])
        a._set_query("nothing matches this")
        self.assertEqual(a._shown(), [])
        w.close()

    def test_pretend_sync_finishes(self):
        w = self.q.MainWindow("light")
        w._pretend_sync()
        self.assertFalse(w.sync.isEnabled())
        for _ in range(100):
            w._tick()
        self.assertTrue(w.sync.isEnabled())
        self.assertEqual(w.status.text(), "Up to date")
        w.close()


if __name__ == "__main__":
    unittest.main()
