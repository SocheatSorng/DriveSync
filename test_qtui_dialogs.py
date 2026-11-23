"""Tests for the Qt dialogs: the Drive folder browser (and, later, the folder dialog), offscreen.
Drive calls go to a fake Drive and run straight away instead of on a thread.
Run:  python -m unittest test_qtui_dialogs -v
"""
import os
import queue
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QPushButton
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui.drive_browser import DriveBrowser
    from test_qtui import dispose

from accounts import NeedsSignIn

A = "teacher@example.com"


class FakeDrive:
    def __init__(self):
        self.folders = {"root": [{"id": "s1", "name": "School"}, {"id": "v1", "name": "View only",
                                                                 "capabilities": {"canAddChildren": False}}],
                        "s1": [{"id": "t1", "name": "Term 1"}], "t1": []}
        self.created = []

    def list_subfolders(self, fid):
        return self.folders[fid]

    def list_shared_with_me(self):
        return [{"id": "sh1", "name": "From a colleague", "capabilities": {"canAddChildren": True}}]

    def list_shared_drives(self):
        return [{"id": "d1", "name": "Staff"}]

    def create_folder(self, name, parent):
        self.created.append((name, parent))
        self.folders[f"new-{name}"] = []
        return f"new-{name}"


def app_with(drive):
    app = mock.MagicMock()
    app.ui = queue.Queue()
    app.needs_signin = set()
    app.drive_for_browsing.return_value = drive
    return app


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class DriveBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def browser(self, drive=None):
        drive = drive or FakeDrive()
        app = app_with(drive)
        b = DriveBrowser(None, app, A, run_now=True)
        self.addCleanup(dispose, b)
        return app, drive, b

    def rows(self, b):
        return [w.text() for w in b.findChildren(QPushButton) if w.property("role") == "row"]

    def messages(self, b):
        return [w.text() for w in b.list_box.findChildren(QLabel)]

    def test_starts_in_my_drive(self):
        _app, _d, b = self.browser()
        self.assertEqual(b.path_lbl.text(), "My Drive")
        self.assertEqual(self.rows(b), ["📁  School", "📁  View only"])
        self.assertTrue(b.select_btn.isEnabled())

    def test_open_choose_and_back(self):
        _app, _d, b = self.browser()
        b.open(("s1", "School", True))
        self.assertEqual(b.path_lbl.text(), "My Drive/School")
        self.assertEqual(self.rows(b), ["📁  Term 1"])
        b.select_btn.click()
        self.assertEqual(b.result, ("s1", "My Drive/School"))
        b.go_up()
        b.go_up()                                           # the top: My Drive, Shared with me, Shared drives
        self.assertEqual(self.rows(b), ["📁  My Drive", "📁  Shared with me", "📁  Shared drives"])
        self.assertFalse(b.select_btn.isEnabled())
        self.assertFalse(b.up_btn.isEnabled())

    def test_shared_places(self):
        _app, _d, b = self.browser()
        b.go_up()
        b.open((DriveBrowser.DRIVES, "Shared drives", False))
        self.assertEqual(self.rows(b), ["📁  Staff"])
        b.go_up()
        b.open((DriveBrowser.SHARED, "Shared with me", False))
        self.assertEqual(self.rows(b), ["📁  From a colleague"])

    def test_view_only_folder_cant_be_chosen(self):
        _app, _d, b = self.browser()
        b.open(("v1", "View only", False))
        self.assertFalse(b.select_btn.isEnabled())
        self.assertFalse(b.new_btn.isEnabled())
        self.assertEqual(b.note.text(), "View-only folder - you can't add files here.")

    def test_empty_folder(self):
        _app, _d, b = self.browser()
        b.open(("t1", "Term 1", True))
        self.assertIn("No folders here.", self.messages(b)[0])

    def test_new_folder(self):
        _app, drive, b = self.browser()
        with mock.patch.object(QInputDialog, "getText", return_value=("  Term 2 ", True)):
            b.new_btn.click()
        self.assertEqual(drive.created, [("Term 2", "root")])
        self.assertEqual(b.path_lbl.text(), "My Drive/Term 2")
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            b.new_btn.click()
        self.assertEqual(len(drive.created), 1)

    def test_real_background_thread(self):
        """Without run_now the Drive call runs on a thread and the result arrives through a Qt signal."""
        import time
        app = app_with(FakeDrive())
        b = DriveBrowser(None, app, A)
        self.addCleanup(dispose, b)
        deadline = time.monotonic() + 5
        while not self.rows(b) and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.rows(b), ["📁  School", "📁  View only"])

    def test_errors(self):
        class Broken(FakeDrive):
            def list_subfolders(self, fid):
                raise NeedsSignIn("The login for teacher@example.com has expired")
        app, _d, b = self.browser(Broken())
        self.assertIn("Sign in again", self.messages(b)[0])
        self.assertIn(A, app.needs_signin)
        self.assertEqual(app.ui.get_nowait(), ("account",))

        class Offline(FakeDrive):
            def list_subfolders(self, fid):
                raise OSError("No internet")
        _app, _d, b = self.browser(Offline())
        self.assertEqual(self.messages(b)[0], "Couldn't load this folder:\nNo internet")


if __name__ == "__main__":
    unittest.main()
