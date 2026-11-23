"""Tests for choosing the window (CustomTkinter or Qt) and for the Qt window shell.
Run:  python -m unittest test_ui_host -v
"""
import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no real window needed

import ui_host

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:  # pragma: no cover - PySide6 is a dependency; this only guards odd setups
    QApplication = None

def dispose(widget) -> None:
    """Delete a widget now: deleteLater() only runs in an event loop, and tests don't run one, so
    otherwise every window a test made stays alive and slows the next tests down."""
    from PySide6.QtCore import QCoreApplication, QEvent
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)



class ChooseUiTests(unittest.TestCase):
    def test_qt_unless_the_classic_window_is_asked_for(self):
        self.assertEqual(ui_host.chosen_ui({}), "qt")
        self.assertEqual(ui_host.chosen_ui({"DRIVESYNC_UI": "tk"}), "tk")
        self.assertEqual(ui_host.chosen_ui({"DRIVESYNC_UI": "  TK "}), "tk")
        self.assertEqual(ui_host.chosen_ui({"DRIVESYNC_UI": "qt"}), "qt")
        self.assertEqual(ui_host.chosen_ui({"DRIVESYNC_UI": "gtk"}), "qt")   # unknown: the default

    def test_host_class(self):
        self.assertIs(ui_host.host_class("tk"), ui_host.TkHost)
        self.assertIs(ui_host.host_class("qt"), ui_host.QtHost)


class FakeApp:
    """Only what the Qt shell reads and calls."""

    def __init__(self, flag: Path):
        self.ui = queue.Queue()
        self.state, self.status, self.detail, self.file_lines = "idle", "Up to date", "Last sync 17:51", []
        self.paused = False
        self.progress = None
        self.activity = mock.Mock()
        self.activity.load.return_value = []
        self.last_check = None
        self.signing_in = None
        self.credentials_ready = lambda: True
        self.accounts_info = lambda: []
        self.show_flag = flag
        self.cfg = {"appearance": "System"}
        self.quit_calls = 0
        self.notes = []
        self.host = None

    def __getattr__(self, name):
        """Anything else the window's screens call (open_log, sync_now...): a stand-in."""
        stand_in = mock.MagicMock(name=name)
        setattr(self, name, stand_in)
        return stand_in

    def quit(self):
        self.quit_calls += 1
        self.host.stop()

    def notify(self, text):
        self.notes.append(text)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class QtHostTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = FakeApp(Path(tmp.name) / ".show")

    def host(self, hidden=True):
        h = ui_host.QtHost(self.app, start_hidden=hidden)
        self.app.host = h
        self.addCleanup(dispose, h.window)
        return h

    def test_starts_hidden_or_shown(self):
        self.assertFalse(self.host(hidden=True).window.isVisible())
        self.assertTrue(self.host(hidden=False).window.isVisible())

    def test_state_message_updates_the_header(self):
        h = self.host()
        self.app.status, self.app.detail = "Syncing…", "Uploading 3 of 12 files"
        self.app.ui.put(("state",))
        h.window.poll()
        self.assertEqual(h.window.header.status_lbl.text(), "Syncing…")
        self.assertIn("Uploading 3 of 12 files", h.window.header.detail_lbl.text())

    def test_show_message_and_show_flag_open_the_window(self):
        h = self.host()
        self.app.ui.put(("show",))
        h.window.poll()
        self.assertTrue(h.window.isVisible())
        h.window.hide()
        self.app.show_flag.touch()                # a second copy asked this one to show itself
        h.window.poll()
        self.assertTrue(h.window.isVisible())
        self.assertFalse(self.app.show_flag.exists())

    def test_error_message_is_shown(self):
        h = self.host()
        self.app.ui.put(("error", "Sign-in didn't finish"))
        with mock.patch.object(QMessageBox, "critical") as box:
            h.window.poll()
        self.assertTrue(h.window.isVisible())
        self.assertEqual(box.call_args.args[2], "Sign-in didn't finish")

    def test_quit_message_quits_the_app(self):
        h = self.host()
        self.app.ui.put(("quit",))
        self.app.ui.put(("state",))               # not handled after quitting
        h.window.poll()
        self.assertEqual(self.app.quit_calls, 1)
        self.assertEqual(self.app.ui.qsize(), 1)

    def test_closing_hides_to_the_tray_and_says_so_once(self):
        h = self.host(hidden=False)
        h.window.close()
        self.assertFalse(h.window.isVisible())
        h.window.show_window()
        h.window.close()
        self.assertEqual(self.app.notes, ["Drive Sync is still running in the system tray."])
        self.assertFalse(QApplication.instance().quitOnLastWindowClosed())   # the app keeps running

    def test_messages_for_screens_not_built_yet_are_ignored(self):
        h = self.host()
        for kind in ("something_new", "not_a_screen"):     # kinds the window doesn't know
            self.app.ui.put((kind, None))
        h.window.poll()                            # no error
        self.assertTrue(self.app.ui.empty())

    def test_errors_in_window_handlers_go_to_the_log(self):
        h = self.host()
        old = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", old)
        h.install_error_hook()

        def boom():
            raise RuntimeError("button handler failed")
        with self.assertLogs("drive_sync", "ERROR") as logs:
            QTimer.singleShot(0, boom)
            for _ in range(5):
                QApplication.processEvents()
        self.assertIn("Error in the window", logs.output[0])

    def test_poll_runs_faster_while_the_window_is_open(self):
        h = self.host(hidden=True)
        h.window.poll()
        self.assertEqual(h.window.timer.interval(), h.window.POLL_HIDDEN_MS)
        h.window.show_window()
        h.window.poll()
        self.assertEqual(h.window.timer.interval(), h.window.POLL_VISIBLE_MS)


if __name__ == "__main__":
    unittest.main()
