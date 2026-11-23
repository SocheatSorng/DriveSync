"""Tests for the Qt Activity tab (qtui/activity_tab.py), offscreen.
Run:  python -m unittest test_qtui_activity -v
"""
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox, QPushButton
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui.activity_tab import ActivityTab
    from qtui.main_window import MainWindow
    from test_qtui import dispose, fake_app

NOW = time.time()


def run(i, items, trig="changes", more=0):
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"type": "run", "ts": NOW - i * 60, "folder": rf"D:\Folder{i}", "trigger": trig,
            "account": "teacher@example.com", "items": items, "counts": counts, "more": more}


def entries():
    """Oldest first, as ActivityStore.load() returns them."""
    return [
        run(3, [{"kind": "uploaded", "path": "Notes/a.docx"}]),
        {"type": "event", "ts": NOW - 120, "level": "info", "message": "Signed in: teacher@example.com",
         "detail": "Google account is ready to sync"},
        run(1, [{"kind": "error", "path": "Micomet", "message": "Couldn't read folder"}] +
            [{"kind": "downloaded", "path": f"Photos/IMG_{k}.jpg"} for k in range(5)], "startup"),
    ]


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class ActivityTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def tab(self, data=None):
        app = fake_app()
        app.activity.load.return_value = entries() if data is None else data
        app.last_check = {"ts": NOW, "folders": 2, "errors": 1, "changes": 6, "trigger": "manual"}
        t = ActivityTab(app)
        self.addCleanup(dispose, t)
        return app, t

    def texts(self, t):
        return [lb.text() for lb in t.findChildren(QLabel)]

    def button(self, t, text):
        return next(b for b in t.findChildren(QPushButton) if b.text() == text)

    def badges(self, t):
        return [lb.text() for lb in t.findChildren(QLabel) if lb.property("role") == "badge"]

    def test_empty(self):
        _app, t = self.tab([])
        self.assertIn("No activity yet", self.texts(t))

    def test_newest_first_with_the_problem_run_open(self):
        _app, t = self.tab()
        texts = self.texts(t)
        self.assertEqual([x for x in texts if x in ("Folder1", "Folder3", "Signed in: teacher@example.com")],
                         ["Folder1", "Signed in: teacher@example.com", "Folder3"])
        self.assertIn("TODAY", texts)
        self.assertEqual(self.badges(t), ["Problem"] + ["Downloaded"] * 5)   # problems first
        self.assertIn("Couldn't read folder", texts)
        self.assertIn("Show 1 item ▾", [b.text() for b in t.findChildren(QPushButton)])
        self.assertIn("see Problems", t.header_lbl.text())

    def test_open_and_close_details(self):
        _app, t = self.tab()
        self.button(t, "Show 1 item ▾").click()
        self.assertIn("Uploaded", self.badges(t))
        self.button(t, "Hide details ▴").click()        # the first open card is the problem run
        self.assertNotIn("Problem", self.badges(t))

    def test_filters_and_search(self):
        _app, t = self.tab()
        self.button(t, "Problems").click()
        texts = self.texts(t)
        self.assertIn("Folder1", texts)
        self.assertNotIn("Folder3", texts)
        self.button(t, "All").click()
        t.findChild(QLineEdit).setText("notes/a")
        t.apply_search()                                 # (typing waits 250 ms before searching)
        texts = self.texts(t)
        self.assertEqual([x for x in texts if x.startswith("Folder")], ["Folder3"])
        t.findChild(QLineEdit).setText("nothing like this")
        t.apply_search()
        self.assertIn("Nothing found", self.texts(t))

    def test_show_older(self):
        many = [run(i, [{"kind": "uploaded", "path": f"f{i}"}]) for i in range(45, 0, -1)]
        _app, t = self.tab(many)
        self.assertEqual(len(t.cards), 40)
        self.button(t, "Show older activity (5 more)").click()
        self.assertEqual(len(t.cards), 45)

    def test_clear_asks_first(self):
        app, t = self.tab()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.button(t, "Clear").click()
        app.activity.clear.assert_not_called()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.button(t, "Clear").click()
        app.activity.clear.assert_called_once()
        self.assertIn("No activity yet", self.texts(t))

    def test_log_file(self):
        app, t = self.tab()
        self.button(t, "Log file").click()
        app.open_log.assert_called_once()

    def test_live_messages(self):
        app = fake_app()
        app.activity.load.return_value = []
        app.last_check = None
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        app.ui.put(("activity", run(0, [{"kind": "uploaded", "path": "new.txt"}])))
        app.last_check = {"ts": NOW, "folders": 1, "errors": 0, "changes": 1, "trigger": "changes"}
        app.ui.put(("last_check",))
        w.poll()
        self.assertEqual(len(w.activity.cards), 1)
        self.assertIn("1 change", w.activity.header_lbl.text())


if __name__ == "__main__":
    unittest.main()
