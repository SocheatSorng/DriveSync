"""Tests for the Qt window's screens (qtui/), offscreen.
Run:  python -m unittest test_qtui -v
"""
import os
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

    from qtui import style
    from qtui.folders_tab import FoldersTab
    from qtui.header import Header
    from qtui.main_window import MainWindow

def dispose(widget) -> None:
    """Delete a widget now: deleteLater() only runs in an event loop, and tests don't run one, so
    otherwise every window a test made stays alive and slows the next tests down."""
    from PySide6.QtCore import QCoreApplication, QEvent
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)



def fake_app(**kw):
    app = mock.MagicMock()
    app.state, app.paused, app.status, app.detail = "idle", False, "Up to date", "Last sync 17:51"
    app.file_lines, app.progress = [], None
    app.cfg = {"appearance": "System"}
    app.ui = queue.Queue()
    app.show_flag = Path(tempfile.gettempdir()) / "ds-test-never-there.show"
    app.activity.load.return_value = []
    app.last_check = None
    for k, v in kw.items():
        setattr(app, k, v)
    return app


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])


class ThemeTests(QtTestCase):
    def test_theme_name(self):
        self.assertEqual(style.theme_name("System", system_dark=True), "dark")
        self.assertEqual(style.theme_name("System", system_dark=False), "light")
        self.assertEqual(style.theme_name("Light", system_dark=True), "light")
        self.assertEqual(style.theme_name("Dark", system_dark=False), "dark")
        self.assertEqual(style.theme_name("whatever", system_dark=True), "dark")   # unknown: follow Windows

    def test_apply_sets_the_stylesheet_and_tones(self):
        style.apply(self.qapp, "Dark")
        self.assertEqual(style.current_name(), "dark")
        self.assertIn(style.THEMES["dark"]["window"], self.qapp.styleSheet())
        self.assertEqual(style.tone("error"), style.THEMES["dark"]["red"])
        style.apply(self.qapp, "Light")
        self.assertIn(style.THEMES["light"]["window"], self.qapp.styleSheet())
        self.assertEqual(style.tone("muted"), style.THEMES["light"]["muted"])
        self.assertIsNone(style.tone(None))

    def test_every_tone_and_state_has_a_colour_in_both_themes(self):
        for name in ("light", "dark"):
            for key in ("error", "waiting", "muted", "link", "ok"):
                self.assertIn(style.TONE_KEYS[key], style.THEMES[name])
        for state in ("idle", "syncing", "error", "paused"):
            self.assertIn(f'[state="{state}"]', style.stylesheet("light"))


class HeaderTests(QtTestCase):
    def make(self, **kw):
        app = fake_app(**kw)
        h = Header(app)
        h.refresh()
        self.addCleanup(dispose, h)
        return app, h

    def test_idle(self):
        _app, h = self.make()
        self.assertEqual(h.status_lbl.text(), "Up to date")
        self.assertEqual(h.detail_lbl.text(), "Last sync 17:51")
        self.assertEqual(h.pause_btn.text(), "Pause")
        self.assertTrue(h.sync_btn.isEnabled())
        self.assertTrue(h.bar.isHidden())
        self.assertEqual(h.dot.property("state"), "idle")
        self.assertEqual(h.dot.accessibleName(), "Status: Up to date")

    def test_syncing_shows_files_and_progress(self):
        _app, h = self.make(state="syncing", status="Syncing…", detail="Uploading 3 of 10",
                            file_lines=["a.jpg", "b.jpg"], progress=types.SimpleNamespace(total=10, percent=40.0))
        self.assertEqual(h.detail_lbl.text(), "Uploading 3 of 10\na.jpg\nb.jpg")
        self.assertFalse(h.sync_btn.isEnabled())
        self.assertFalse(h.bar.isHidden())
        self.assertEqual(h.bar.value(), 400)
        self.assertEqual(h.dot.property("state"), "syncing")

    def test_paused(self):
        _app, h = self.make(paused=True, status="Paused")
        self.assertEqual(h.pause_btn.text(), "Resume")
        self.assertEqual(h.dot.property("state"), "paused")

    def test_buttons_call_the_app(self):
        app, h = self.make()
        h.pause_btn.click()
        app.toggle_pause.assert_called_once()
        h.sync_btn.click()
        app.sync_now.assert_called_once()


class MainWindowHeaderTests(QtTestCase):
    def test_state_messages_refresh_the_header(self):
        app = fake_app()
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        app.state, app.status, app.detail = "error", "Couldn't sync", "No internet"
        app.ui.put(("state",))
        w.poll()
        self.assertEqual(w.header.status_lbl.text(), "Couldn't sync")
        self.assertEqual(w.header.dot.property("state"), "error")

    def test_window_uses_the_appearance_setting(self):
        w = MainWindow(fake_app(cfg={"appearance": "Dark"}))
        self.addCleanup(dispose, w)
        self.assertEqual(style.current_name(), "dark")



EMAIL = "teacher@example.com"


def folders_app(folders, tmp, signin=()):
    app = fake_app(cfg={"appearance": "Light", "folders": folders})
    app.accounts_info.return_value = [{"email": EMAIL, "needs_signin": EMAIL in signin, "folders": len(folders)}]
    app.folder_account.side_effect = lambda f, known: f.get("account")
    app.is_waiting.side_effect = lambda f: bool(f.get("waiting"))
    app.remote_ids = {}
    return app


class FoldersTabTests(QtTestCase):
    def setUp(self):
        import tempfile as _t
        d = _t.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        (self.tmp / "Image").mkdir()
        (self.tmp / "Image" / "Old drafts").mkdir()
        self.folders = [
            {"local": str(self.tmp / "Image"), "account": EMAIL, "mode": "two_way", "drive_folder": "PC Backup/Image",
             "exclude": ["*.log", "Old drafts"]},
            {"local": str(self.tmp / "New"), "account": EMAIL, "mode": "two_way", "drive_folder": "New",
             "waiting": True},
        ]

    def tab(self, folders=None, **kw):
        app = folders_app(self.folders if folders is None else folders, self.tmp, **kw)
        t = FoldersTab(app)
        t.refresh()
        self.addCleanup(dispose, t)
        return app, t

    def texts(self, w):
        return [lb.text() for lb in w.findChildren(QLabel)]

    def buttons(self, w, text):
        return [b for b in w.findChildren(QPushButton) if b.text() == text]

    def test_empty(self):
        _app, t = self.tab([])
        self.assertIn("Back up your first folder", self.texts(t))
        self.assertEqual(t.cards, [])

    def test_cards_show_the_shared_texts(self):
        _app, t = self.tab()
        self.assertEqual(len(t.cards), 2)
        texts = self.texts(t.cards[0])
        self.assertIn("Image", texts)
        self.assertIn(f"↔  {EMAIL} · My Drive/PC Backup/Image · two-way", texts)
        self.assertIn("*.log  ·  Rule", texts)
        self.assertIn("Old drafts  ·  This PC", texts)
        missing = self.texts(t.cards[1])                       # the waiting folder isn't on this PC
        self.assertIn("⚠  This folder no longer exists on this PC", missing)

    def test_start_syncing(self):
        (self.tmp / "New").mkdir()
        app, t = self.tab()
        start = self.buttons(t.cards[1], "Start syncing")
        self.assertEqual(len(start), 1)
        self.assertTrue(start[0].isEnabled())
        self.assertEqual(self.buttons(t.cards[0], "Start syncing"), [])
        start[0].click()
        app.start_folder.assert_called_once_with(self.folders[1])

    def test_start_syncing_disabled_until_signed_in(self):
        (self.tmp / "New").mkdir()
        _app, t = self.tab(signin=(EMAIL,))
        self.assertFalse(self.buttons(t.cards[1], "Start syncing")[0].isEnabled())

    def test_remove_asks_first(self):
        app, t = self.tab()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.buttons(t.cards[0], "Remove")[0].click()
        app.save_config.assert_not_called()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as q:
            self.buttons(t.cards[0], "Remove")[0].click()
        self.assertIn("Stop syncing this folder?", q.call_args.args[2])
        saved = app.save_config.call_args.args[0]
        self.assertEqual([f["local"] for f in saved["folders"]], [self.folders[1]["local"]])

    def test_open_and_drive(self):
        app, t = self.tab()
        with mock.patch("os.startfile") as start:
            self.buttons(t.cards[0], "Open")[0].click()
        start.assert_called_once_with(self.folders[0]["local"])
        self.buttons(t.cards[0], "Drive")[0].click()
        app.open_url.assert_called_with("https://drive.google.com/drive/my-drive?authuser=teacher%40example.com", EMAIL)
        self.buttons(t, "Open Google Drive")[0].click()
        app.open_url.assert_called_with("https://drive.google.com/drive/my-drive?authuser=teacher%40example.com", EMAIL)

    def test_open_does_nothing_for_a_missing_folder(self):
        _app, t = self.tab()
        with mock.patch("os.startfile") as start:
            self.buttons(t.cards[1], "Open")[0].click()
        start.assert_not_called()

    def test_account_message_refreshes_folders(self):
        app = folders_app(self.folders, self.tmp)
        w = MainWindow(app)
        self.addCleanup(dispose, w)
        self.assertEqual(len(w.folders.cards), 2)
        app.cfg = {**app.cfg, "folders": self.folders[:1]}
        app.ui.put(("account",))
        w.poll()
        self.assertEqual(len(w.folders.cards), 1)

if __name__ == "__main__":
    unittest.main()
