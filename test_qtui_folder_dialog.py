"""Tests for the Qt Add / Edit folder dialog and the Folders tab's Add and Edit, offscreen.
Run:  python -m unittest test_qtui_folder_dialog -v
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox, QPushButton
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui.drive_browser import DriveBrowser
    from qtui.folder_dialog import FolderDialog
    from qtui.folders_tab import FoldersTab
    from test_qtui import dispose, fake_app

A, B = "teacher@example.com", "family@example.com"


def dialog_app(accounts=(A, B)):
    app = mock.MagicMock()
    app.accounts_info.return_value = [{"email": e, "needs_signin": False, "folders": 0} for e in accounts]
    app.folder_account.side_effect = lambda f, known: f.get("account")
    return app


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class FolderDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def dialog(self, data=None, app=None):
        d = FolderDialog(None, app or dialog_app(), "Edit folder" if data else "Add folder", data, auto_browse=False)
        self.addCleanup(dispose, d)
        return d

    def test_add_with_defaults(self):
        d = self.dialog()
        self.assertEqual(d.account.currentText(), A)
        self.assertEqual(d.mode_value(), "two_way")
        self.assertTrue(d.mirror.isHidden())
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self.dir):
            d.browse_btn.click()
        name = os.path.basename(self.dir)
        self.assertEqual(d.drive.text(), "My Drive/PC Backup/" + name)
        d.exclude.setText("*.log")
        d.save_btn.click()
        self.assertEqual(d.result, {"local": os.path.normpath(self.dir), "account": A, "mode": "two_way",
                                    "delete": False, "exclude": ["*.log"], "drive_folder": "PC Backup/" + name})

    def test_edit_is_prefilled(self):
        data = {"local": self.dir, "account": B, "mode": "upload", "delete": True,
                "exclude": ["*.tmp", "Old"], "drive_folder": "Backups/X", "waiting": True}
        d = self.dialog(data)
        self.assertEqual(d.local.text(), self.dir)
        self.assertEqual(d.account.currentText(), B)
        self.assertEqual(d.drive.text(), "My Drive/Backups/X")
        self.assertEqual(d.exclude.text(), "*.tmp, Old")
        self.assertEqual(d.mode_value(), "upload")
        self.assertFalse(d.mirror.isHidden())
        self.assertTrue(d.mirror.isChecked())
        d.save_btn.click()
        self.assertTrue(d.result["delete"])
        self.assertTrue(d.result["waiting"])                      # other keys are kept

    def test_a_removed_account_still_shows(self):
        d = self.dialog({"local": self.dir, "account": "old@example.com", "drive_folder": "X"})
        self.assertEqual(d.account.currentText(), "old@example.com")

    def test_switching_to_two_way_hides_mirror(self):
        d = self.dialog({"local": self.dir, "account": A, "mode": "upload", "delete": True, "drive_folder": "X"})
        d.set_mode("two_way")
        self.assertTrue(d.mirror.isHidden())
        d.save_btn.click()
        self.assertFalse(d.result["delete"])

    def test_choose_drive_folder_and_switch_account(self):
        d = self.dialog()
        d.local.setText(self.dir)
        with mock.patch.object(DriveBrowser, "ask", return_value=("sd1", "Shared drive › Staff")) as ask:
            d.choose_drive_btn.click()
        self.assertEqual(ask.call_args.args[2], A)
        self.assertEqual(d.drive.text(), "Shared drive › Staff")
        d.save_btn.click()
        self.assertEqual(d.result["drive_folder_id"], "sd1")
        d2 = self.dialog()
        with mock.patch.object(DriveBrowser, "ask", return_value=("sd1", "Shared drive › Staff")):
            d2.choose_drive_btn.click()
        d2.account.setCurrentText(B)                               # a chosen folder belongs to one account
        self.assertEqual(d2.drive.text(), "")
        self.assertIsNone(d2.drive_id)

    def test_no_account(self):
        d = self.dialog(app=dialog_app(accounts=()))
        self.assertTrue(d.account.isHidden())
        with mock.patch.object(QMessageBox, "information") as info:
            d.choose_drive_btn.click()
        self.assertEqual(info.call_args.args[2], "Add a Google account in Settings first.")

    def test_choose_skips(self):
        d = self.dialog({"local": self.dir, "account": A, "drive_folder": "X", "exclude": ["*.log"]})
        self.assertFalse(d.skips_btn.isHidden())
        with mock.patch("qtui.skip_picker.pick_skips", return_value=(["*.log", "Old"], {"Old": "pc"})) as pick:
            d.skips_btn.click()
        args = pick.call_args.args
        self.assertEqual((args[2], args[3], args[4]), (self.dir, "*.log", A))
        self.assertEqual(d.exclude.text(), "*.log, Old")
        d.save_btn.click()
        self.assertEqual(d.result["exclude_where"], {"Old": "pc"})

    def test_missing_pc_folder(self):
        d = self.dialog()
        d.local.setText(os.path.join(self.dir, "nope"))
        with mock.patch.object(QMessageBox, "critical") as crit:
            d.save_btn.click()
        self.assertEqual(crit.call_args.args[2], "Please choose a folder that exists on this PC.")
        self.assertIsNone(d.result)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class FoldersTabAddEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def tab(self, folders):
        app = fake_app(cfg={"appearance": "Light", "folders": folders})
        app.accounts_info.return_value = [{"email": A, "needs_signin": False, "folders": len(folders)}]
        app.folder_account.side_effect = lambda f, known: f.get("account")
        app.is_waiting.side_effect = lambda f: bool(f.get("waiting"))
        app.save_config.side_effect = lambda c: setattr(app, "cfg", c)
        app.remote_ids = {}
        t = FoldersTab(app)
        t.refresh()
        self.addCleanup(dispose, t)
        return app, t

    def button(self, w, text):
        return next(b for b in w.findChildren(QPushButton) if b.text() == text)

    def test_add_folder_waits_and_duplicates_are_refused(self):
        app, t = self.tab([{"local": r"D:\EBC", "account": A}])
        with mock.patch.object(FolderDialog, "ask", return_value={"local": r"D:\Photos", "account": A}):
            self.button(t, "+  Add folder").click()
        self.assertEqual(app.cfg["folders"][-1], {"local": r"D:\Photos", "account": A, "waiting": True})
        self.assertEqual(len(t.cards), 2)
        with mock.patch.object(FolderDialog, "ask", return_value={"local": r"d:\ebc", "account": A}), \
                mock.patch.object(QMessageBox, "information") as info:
            self.button(t, "+  Add folder").click()
        self.assertEqual(info.call_args.args[2], "That folder is already being synced to this account.")
        self.assertEqual(len(app.cfg["folders"]), 2)

    def test_empty_state_has_an_add_button(self):
        app, t = self.tab([])
        buttons = [b for b in t.findChildren(QPushButton) if b.text() == "+  Add folder"]
        self.assertEqual(len(buttons), 2)                          # the top one and the empty card's
        with mock.patch.object(FolderDialog, "ask", return_value=None) as ask:
            buttons[-1].click()
        ask.assert_called_once()
        app.save_config.assert_not_called()

    def test_edit_and_skip_chips(self):
        folder = {"local": r"D:\EBC", "account": A, "exclude": ["*.log"]}
        app, t = self.tab([folder])
        new = {"local": r"D:\EBC", "account": A, "exclude": ["*.tmp"]}
        with mock.patch.object(FolderDialog, "ask", return_value=new) as ask:
            self.button(t.cards[0], "Edit").click()
        self.assertEqual(ask.call_args.args[3], folder)
        self.assertEqual(app.cfg["folders"][0]["exclude"], ["*.tmp"])
        chip = next(lb for lb in t.cards[0].findChildren(QLabel) if lb.property("role") == "chip")
        with mock.patch.object(t, "edit_folder") as edit:
            chip.mousePressEvent(None)
        edit.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
