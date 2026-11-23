"""Tests for the Qt Settings tab (qtui/settings_tab.py), offscreen.
Run:  python -m unittest test_qtui_settings -v
"""
import os
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QLabel, QMessageBox, QPushButton
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui import settings_tab, style
    from qtui.main_window import MainWindow
    from qtui.settings_tab import AddAccountDialog, SettingsTab
    from qtui.widgets import Switch
    from test_qtui import dispose, fake_app

A, B = "teacher@example.com", "family@example.com"
PROFILES = [types.SimpleNamespace(id="edge|Default", label="Edge - Me", directory="Default")]


def accounts_app(info=None, ready=True, busy=None):
    app = fake_app()
    app.credentials_ready.return_value = ready
    app.signing_in = busy
    app.accounts_info.return_value = info if info is not None else [
        {"email": A, "needs_signin": False, "folders": 2, "storage": "6.1 GB of 15 GB"},
        {"email": B, "needs_signin": True, "folders": 1},
    ]
    app.account_profile.side_effect = lambda e: "edge|Default" if e == A else None
    return app


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class AccountsSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        p = mock.patch.object(settings_tab.browsers, "list_profiles", return_value=PROFILES)
        p.start()
        self.addCleanup(p.stop)

    def tab(self, **kw):
        app = accounts_app(**kw)
        t = SettingsTab(app)
        t.refresh_accounts()
        self.addCleanup(dispose, t)
        return app, t

    def texts(self, w):
        return [lb.text() for lb in w.findChildren(QLabel)]

    def buttons(self, w, text):
        return [b for b in w.findChildren(QPushButton) if b.text() == text]

    def test_help_text_by_state(self):
        _app, t = self.tab(ready=False)
        self.assertIn("import the OAuth client file", t.accounts_help.text())
        self.assertFalse(t.add_account_btn.isEnabled())
        _app, t = self.tab(info=[])
        self.assertIn("Add the Google account to back up to", t.accounts_help.text())

    def test_rows(self):
        _app, t = self.tab()
        texts = self.texts(t)
        self.assertIn(A, texts)
        self.assertIn("2 folders  ·  Drive: 6.1 GB of 15 GB", texts)
        self.assertIn("Click “Sign in again” to keep syncing (needs updated access)", texts)
        self.assertEqual(len(t.account_rows), 2)
        self.assertEqual(len(self.buttons(t, "Sign in again")), 1)

    def test_sign_in_again(self):
        app, t = self.tab()
        self.buttons(t, "Sign in again")[0].click()
        app.add_account.assert_called_once_with(B)

    def test_remove_asks_first(self):
        app, t = self.tab()
        remove = self.buttons(t.account_rows[0], "Remove")[0]
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            remove.click()
        app.remove_account.assert_not_called()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as q:
            remove.click()
        self.assertIn("2 folders use this account", q.call_args.args[2])
        app.set_account_profile.assert_called_with(A, None)
        app.remove_account.assert_called_once_with(A)

    def test_browser_profile_menu(self):
        app, t = self.tab()
        combo = t.account_rows[0].findChild(QComboBox)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ["Default browser", "Edge - Me"])
        self.assertEqual(combo.currentText(), "Edge - Me")
        combo.setCurrentText("Default browser")
        app.set_account_profile.assert_called_with(A, None)

    def test_add_account_and_cancel(self):
        app, t = self.tab()
        with mock.patch.object(AddAccountDialog, "ask", return_value=(True, "edge|Default")) as ask:
            t.add_account_btn.click()
        self.assertIn("Edge - Me", ask.call_args.args[1])
        app.add_account.assert_called_once_with(browser_profile="edge|Default")
        with mock.patch.object(AddAccountDialog, "ask", return_value=(False, None)):
            t.add_account_btn.click()
        self.assertEqual(app.add_account.call_count, 1)                 # cancelled: no sign-in
        self.assertTrue(t.cancel_btn.isHidden())

    def test_busy_shows_cancel(self):
        app, t = self.tab(busy="new")
        self.assertFalse(t.add_account_btn.isEnabled())
        self.assertEqual(t.add_account_btn.text(), "Waiting for browser…")
        self.assertFalse(t.cancel_btn.isHidden())
        t.cancel_btn.click()
        app.cancel_sign_in.assert_called_once()

    def test_import_credentials(self):
        app, t = self.tab(info=[])
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(r"C:\Downloads\credentials.json", "")), \
                mock.patch.object(AddAccountDialog, "ask", return_value=(True, None)):
            self.buttons(t, "Import credentials.json…")[0].click()
        app.import_credentials.assert_called_once_with(Path(r"C:\Downloads\credentials.json"))
        app.add_account.assert_called_once_with(browser_profile=None)   # no account yet: straight to sign-in

    def test_import_a_wrong_file(self):
        app, t = self.tab()
        app.import_credentials.side_effect = ValueError("That doesn't look like a Desktop-app OAuth client file.")
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(r"C:\x.json", "")), \
                mock.patch.object(QMessageBox, "critical") as crit:
            self.buttons(t, "Import credentials.json…")[0].click()
        self.assertIn("OAuth client file", crit.call_args.args[2])
        app.add_account.assert_not_called()

    def test_setup_guide(self):
        app, t = self.tab()
        self.buttons(t, "Setup guide")[0].click()
        app.open_readme.assert_called_once()

    def test_account_added_message(self):
        app = accounts_app()
        app.cfg = {**app.cfg, "folders": [{"local": "A"}]}
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        app.ui.put(("account_added", A, "edge|Default"))
        w.poll()
        app.set_account_profile.assert_called_with(A, "edge|Default")
        self.assertEqual(app.save_config.call_args.args[0]["folders"], [{"local": "A", "account": A}])
        app.notify.assert_called_with(f"Google account added: {A}")



@unittest.skipIf(QApplication is None, "PySide6 not installed")
class OptionsSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        for target, value in ((settings_tab.browsers, ("list_profiles", PROFILES)),
                              (settings_tab.updater, ("installed_copy", True))):
            p = mock.patch.object(target, value[0], return_value=value[1])
            p.start()
            self.addCleanup(p.stop)

    def tab(self, **cfg):
        app = accounts_app()
        app.cfg = {"appearance": "Light", "interval_minutes": 15, "drive_poll_seconds": 10, "speed_limit_kbps": 0,
                   "watch_changes": True, "notify_on_changes": False, "high_priority": False,
                   "pause_on_metered": False, "auto_update": True, "folders": [], **cfg}
        app.save_config.side_effect = lambda c: setattr(app, "cfg", c)
        app.startup_enabled.return_value = True
        app.update, app.update_status = None, ""
        t = SettingsTab(app)
        self.addCleanup(dispose, t)
        return app, t

    def switch(self, t, text):
        return next(w for w in t.findChildren(Switch) if w.text() == text)

    def test_shows_the_saved_options(self):
        _app, t = self.tab(interval_minutes=60, speed_limit_kbps=2048, high_priority=True, pause_on_metered=True)
        self.assertEqual(t.interval_menu.currentText(), "Every hour")
        self.assertEqual(t.limit_menu.currentText(), "2 MB/s")
        self.assertEqual(t.poll_menu.currentText(), "Every 10 seconds")
        self.assertEqual(t.priority.checkedButton().text(), "High")
        self.assertTrue(self.switch(t, "Don't sync on metered connections").isChecked())
        self.assertTrue(self.switch(t, "Start Drive Sync when I sign in to Windows").isChecked())

    def test_changes_are_saved(self):
        app, t = self.tab()
        t.interval_menu.setCurrentText("Once a day")
        self.assertEqual(app.cfg["interval_minutes"], 1440)
        self.switch(t, "Show a notification when files are uploaded").click()
        self.assertTrue(app.cfg["notify_on_changes"])
        t.limit_menu.setCurrentText("1 MB/s")
        self.assertEqual(app.cfg["speed_limit_kbps"], 1024)
        next(b for b in t.priority.buttons() if b.text() == "High").click()
        self.assertTrue(app.cfg["high_priority"])
        self.switch(t, "Install updates automatically").click()
        self.assertFalse(app.cfg["auto_update"])

    def test_appearance(self):
        app, t = self.tab()
        next(b for b in t.appearance.buttons() if b.text() == "Dark").click()
        self.assertEqual(app.cfg["appearance"], "Dark")
        self.assertEqual(style.current_name(), "dark")
        next(b for b in t.appearance.buttons() if b.text() == "Light").click()
        self.assertEqual(style.current_name(), "light")

    def test_start_with_windows(self):
        app, t = self.tab()
        sw = self.switch(t, "Start Drive Sync when I sign in to Windows")
        app.startup_enabled.return_value = False
        sw.click()
        app.set_startup.assert_called_once_with(False)
        self.assertFalse(sw.isChecked())
        app.set_startup.side_effect = OSError("Access denied")
        with mock.patch.object(QMessageBox, "critical") as crit:
            sw.click()
        self.assertIn("Access denied", crit.call_args.args[2])
        self.assertFalse(sw.isChecked())                     # shows what Windows really has

    def test_updates(self):
        app, t = self.tab()
        self.assertEqual(t.update_lbl.text(), "Version " + settings_tab.__version__ + " (installed)")
        self.assertTrue(t.update_btn.isHidden())
        app.update, app.update_status = types.SimpleNamespace(version="9.9.9"), "Checked 17:00"
        t.refresh_update()
        self.assertEqual(t.update_btn.text(), "Update to 9.9.9")
        self.assertFalse(t.update_btn.isHidden())
        t.update_btn.click()
        app.install_update.assert_called_once()
        next(b for b in t.findChildren(QPushButton) if b.text() == "Check now").click()
        app.check_updates_now.assert_called_once()

    def test_update_message_refreshes(self):
        app = accounts_app()
        app.update, app.update_status = types.SimpleNamespace(version="9.9.9"), ""
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        app.ui.put(("update",))
        w.poll()
        self.assertEqual(w.settings.update_btn.text(), "Update to 9.9.9")

    def test_quit_and_uninstall(self):
        app, t = self.tab()
        quit_btn = next(b for b in t.findChildren(QPushButton) if b.text() == "Quit Drive Sync")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            quit_btn.click()
        app.quit.assert_not_called()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            quit_btn.click()
        app.quit.assert_called_once()
        next(b for b in t.findChildren(QPushButton) if b.text() == "Uninstall…").click()
        app.uninstall.assert_called_once()

    def test_no_uninstall_button_when_not_installed(self):
        with mock.patch.object(settings_tab.updater, "installed_copy", return_value=False):
            _app, t = self.tab()
        self.assertEqual([b for b in t.findChildren(QPushButton) if b.text() == "Uninstall…"], [])
        self.assertIn("not installed with Setup", t.update_lbl.text())

if __name__ == "__main__":
    unittest.main()
