"""Tests for the Qt Space tab (qtui/space_tab.py), offscreen. No real scan or delete runs: the
background work is handed to a fake runner.
Run:  python -m unittest test_qtui_space -v
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QLabel, QMessageBox, QPushButton
except ImportError:  # pragma: no cover
    QApplication = None

import space_model as sm
from space import DuplicateGroup, Item, Report

if QApplication is not None:
    from qtui.main_window import MainWindow
    from qtui.space_tab import SpaceTab
    from test_qtui import dispose, fake_app

A = "teacher@example.com"


def it(k, size, **kw):
    return Item(k, k, size, **kw)


def pc_report():
    return Report("pc", r"D:\Storage", total=9_000, files=5,
                  junk=[it(r"D:\Storage\Thumbs.db", 40)],
                  duplicates=[DuplicateGroup(400, [it(r"D:\Storage\a.jpg", 400), it(r"D:\Storage\Copy\a.jpg", 400)])],
                  biggest_files=[it(r"D:\Storage\big.mp4", 8_000)],
                  biggest_folders=[it(r"D:\Storage\Video", 8_000, is_dir=True)])


def drive_report():
    return Report("drive", A, total=1_000, files=2, trash=500, quota={"limit": 15_000, "usage": 14_000},
                  junk=[it("j1", 30, extra={"used": 30})], old_versions=[it("o1", 300)],
                  biggest_files=[it("b1", 800, extra={"used": 800, "parent": "p1"})])


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class SpaceTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def tab(self, report=None, busy=False, email=None):
        app = fake_app()
        app.accounts_info.return_value = [{"email": A, "needs_signin": False, "folders": 1}]
        st = sm.SpaceState()
        if report is not None:
            st.report = report
            st.selected = {x.key for x in report.safe()}
        st.busy, st.email = busy, email
        app.space = st
        t = SpaceTab(app)
        t.runs = []
        t.run_in_background = lambda fn, *args: t.runs.append((fn, args))
        self.addCleanup(dispose, t)
        return app, t

    def texts(self, t):
        return [w.text() for w in t.findChildren(QLabel)] + [w.text() for w in t.findChildren(QCheckBox)]

    def button(self, t, text):
        return next(b for b in t.findChildren(QPushButton) if b.text() == text)

    def test_intro_and_sources(self):
        _app, t = self.tab()
        self.assertEqual(t.status_lbl.text(), sm.INTRO)
        self.assertEqual([t.source.itemText(i) for i in range(t.source.count())], [sm.SYNCED, sm.PICK, sm.DRIVE + A])
        self.assertTrue(t.stop_btn.isHidden())

    def test_pc_report(self):
        _app, t = self.tab(pc_report())
        texts = self.texts(t)
        self.assertIn("This PC", texts)
        self.assertTrue(any(x.startswith("Safe to delete - ") for x in texts))
        self.assertTrue(any("(junk)" in x for x in texts))
        self.assertIn("2 copies · 400 B each", texts)
        self.assertEqual(t.delete_btn.text(), "Move 2 selected (440 B) to Recycle Bin")

    def test_ticking_changes_the_selection(self):
        app, t = self.tab(pc_report())
        junk = next(c for c in t.findChildren(QCheckBox) if "(junk)" in c.text())
        junk.click()
        self.assertNotIn(r"D:\Storage\Thumbs.db", app.space.selected)
        self.assertEqual(t.delete_btn.text(), "Move 1 selected (400 B) to Recycle Bin")

    def test_delete_asks_first_then_runs_in_the_background(self):
        app, t = self.tab(pc_report())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            t.delete_btn.click()
        self.assertEqual(t.runs, [])
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            t.delete_btn.click()
        fn, args = t.runs[0]
        self.assertIs(fn, sm.run_delete)
        self.assertEqual(args[2], {r"D:\Storage\Thumbs.db", r"D:\Storage\Copy\a.jpg"})
        self.assertTrue(app.space.busy)
        self.assertFalse(t.delete_btn.isEnabled())

    def test_every_copy_selected_warns(self):
        app, t = self.tab(pc_report())
        app.space.selected.add(r"D:\Storage\a.jpg")
        t.render()
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No) as warn:
            t.delete_btn.click()
        self.assertIn("none of them would be kept", warn.call_args.args[2])

    def test_drive_report_old_versions_and_trash(self):
        app, t = self.tab(drive_report(), email=A)
        self.assertIn("Google Drive: 13.7 KB of 14.6 KB used (93%) - this includes Gmail and Google Photos.",
                      "\n".join(self.texts(t)))
        self.button(t, "Drive's trash uses 500 B - open it").click()
        app.open_url.assert_called_with(sm.DRIVE_TRASH_URL, A)
        old = next(c for c in t.findChildren(QCheckBox) if c.text().strip().endswith("o1"))
        old.click()
        self.assertEqual(t.old_btn.text(), "Remove old versions of 1 file")
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes):
            t.old_btn.click()
        self.assertIs(t.runs[0][0], sm.run_old)

    def test_open_items(self):
        app, t = self.tab(drive_report(), email=A)
        opens = [b for b in t.findChildren(QPushButton) if b.text() == "Open"]
        opens[0].click()
        app.open_url.assert_called_with("https://drive.google.com/drive/folders/p1", A)

    def test_scan_synced_folders(self):
        app, t = self.tab()
        app.cfg = {**app.cfg, "folders": []}
        with mock.patch.object(QMessageBox, "information") as info:
            t.scan_btn.click()
        self.assertEqual(info.call_args.args[2], sm.NO_SYNCED_FOLDERS)
        self.assertEqual(t.runs, [])
        app.cfg = {**app.cfg, "folders": [{"local": os.getcwd()}]}
        t.scan_btn.click()
        fn, args = t.runs[0]
        self.assertIs(fn, sm.run_scan)
        self.assertEqual(args[2], ("pc", [os.getcwd()]))
        self.assertTrue(app.space.busy)
        self.assertFalse(t.stop_btn.isHidden())
        t.stop_btn.click()
        self.assertTrue(app.space.stop.is_set())

    def test_scan_a_chosen_folder(self):
        _app, t = self.tab()
        t.source.setCurrentText(sm.PICK)
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=""):
            t.scan_btn.click()
        self.assertEqual(t.runs, [])                     # cancelled
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=os.getcwd()):
            t.scan_btn.click()
        self.assertEqual(t.runs[0][1][2], ("pc", [os.path.normpath(os.getcwd())]))

    def test_messages(self):
        app = fake_app()
        app.accounts_info.return_value = []
        app.space = sm.SpaceState()
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        app.space.report = pc_report()
        app.space.selected = {x.key for x in app.space.report.safe()}
        app.ui.put(("space",))
        w.poll()
        self.assertEqual(w.space.delete_btn.text(), "Move 2 selected (440 B) to Recycle Bin")
        app.space.busy, app.space.progress, app.space.status = True, "Moving 1 of 2…", "Moving…: 1 done…"
        app.ui.put(("space_progress",))
        w.poll()
        self.assertEqual(w.space.delete_btn.text(), "Moving 1 of 2…")
        self.assertEqual(w.space.status_lbl.text(), "Moving…: 1 done…")


if __name__ == "__main__":
    unittest.main()
