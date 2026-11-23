"""Tests for new folders that wait for "Start syncing" before their first sync.
Run:  python -m unittest test_waiting_folders -v
"""
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import tray_app
from watcher import ChangeTracker

EMAIL = "teacher@example.com"
OLD = {"local": r"C:\School", "account": EMAIL, "mode": "two_way"}           # already syncing
NEW = {"local": r"D:\EBC", "account": EMAIL, "mode": "two_way", "waiting": True}


class FakeDrive:
    def __init__(self):
        self.started = 0

    def changes_start_token(self):
        self.started += 1
        return "1"

    def changes_since(self, token):
        return {"changed-file"}, "2"


class WaitingFolderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = self.dir / "config.json"
        self.creds = self.dir / "credentials.json"
        self.creds.write_text("{}", encoding="utf-8")
        for target, value in ((tray_app, ("CONFIG_PATH", self.config)),
                              (tray_app, ("CREDS_PATH", self.creds)),
                              (tray_app, ("SYNC_STATE_PATH", self.dir / "sync_state.json"))):
            p = mock.patch.object(target, *value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(tray_app.accounts, "list_accounts", lambda: [EMAIL])
        p.start()
        self.addCleanup(p.stop)
        self.write_config([OLD, NEW])

    def write_config(self, folders):
        self.config.write_text(json.dumps({"folders": folders}), encoding="utf-8")

    def app(self):
        """An App with just the parts syncing uses (no window, no tray icon)."""
        app = tray_app.App.__new__(tray_app.App)
        app.sync_state = app._load_sync_state()
        app.change_tokens = app.sync_state["tokens"]
        app.changes = ChangeTracker()
        app.drive_marked = set()
        app.needs_signin = set()
        app.drive = FakeDrive()
        app.drives = {EMAIL: app.drive}
        app.cfg = app.load_config()
        app.stopping = threading.Event()
        app.wake = threading.Event()
        app.ui = queue.Queue()
        app.paused = False
        app.watcher = mock.Mock(watched=1)
        app.remote_ids, app.storage_full, app._full_notified = {}, {}, frozenset()
        app.progress, app.file_lines, app.menu_file_lines = None, [], []
        app.states, app.detail = [], ""
        app.set_state = lambda state, status, detail="": app.states.append((state, status, detail))
        app.apply_network = lambda cfg: None
        app.notify = lambda msg: None
        app._record = lambda entry: None
        app._update_quota = lambda email, drive: None
        app._migrate_legacy_token = lambda: None
        return app

    def key(self, f):
        return tray_app.App.folder_key(f, [EMAIL])

    def synced(self, app, **kwargs):
        """Run one sync round; the local folders synced."""
        done = []

        def fake_sync(drive, local, **_kw):
            done.append(local)
            return {"scope": "full"}

        with mock.patch.object(tray_app, "sync_folder", fake_sync), \
                mock.patch.object(tray_app, "count_files", lambda *_a: (0, 0)):
            app._sync_all(**kwargs)
        return done

    # -------------------------------------------------------------- tests
    def test_existing_folders_keep_syncing(self):
        app = self.app()
        self.assertFalse(app.is_waiting(OLD))
        self.assertEqual(app.active_folders(app.cfg), [OLD])
        self.assertEqual(self.synced(app, trigger="scheduled", scan=True), [OLD["local"]])

    def test_full_check_and_sync_now_skip_a_waiting_folder(self):
        app = self.app()
        self.assertEqual(self.synced(app, trigger="manual", full=True, scan=True), [OLD["local"]])
        self.assertEqual(self.synced(app, trigger="scheduled", scan=True), [OLD["local"]])

    def test_change_events_skip_a_waiting_folder(self):
        app = self.app()
        self.assertEqual(self.synced(app, only={self.key(NEW)}, trigger="changes"), [])

    def test_only_waiting_folders_says_press_start(self):
        self.write_config([NEW])
        app = self.app()
        self.assertEqual(self.synced(app, trigger="scheduled", scan=True), [])
        self.assertEqual(app.states[-1][1], "Waiting - press Start syncing")

    def test_waiting_folder_is_not_watched(self):
        app = self.app()
        app.refresh_watchers()
        wanted = app.watcher.update.call_args[0][0]
        self.assertEqual(set(wanted), {self.key(OLD)})

    def test_drive_changes_skip_a_waiting_folder(self):
        self.write_config([NEW])
        app = self.app()
        app._poll_drive()
        app._poll_drive()
        self.assertEqual(app.drive.started, 0)          # its account isn't even asked
        self.assertEqual(app.drive_marked, set())
        self.assertEqual(app.changes.take_settled(), set())

    def test_start_syncing_runs_the_first_sync_and_is_saved(self):
        app = self.app()
        self.assertTrue(app.start_folder(NEW))
        self.assertNotIn("waiting", json.loads(self.config.read_text(encoding="utf-8"))["folders"][1])
        self.assertTrue(app.wake.is_set())
        # The worker picks it up straight away, on its own, as a full check (never synced before).
        changed = app.changes.take_settled()
        self.assertEqual(changed, {self.key(NEW)})
        self.assertIsNone(app._quick_plan(app.cfg["folders"][1], self.key(NEW), EMAIL, False, False))
        self.assertEqual(self.synced(app, only=changed, trigger="changes"), [NEW["local"]])
        # From now on it's an ordinary folder, also after a restart: watched and no button.
        app = self.app()
        self.assertEqual(app.waiting_count(), 0)
        self.assertEqual(len(app.active_folders(app.cfg)), 2)
        app.refresh_watchers()
        self.assertIn(self.key(NEW), app.watcher.update.call_args[0][0])

    def test_start_syncing_a_removed_folder_does_nothing(self):
        self.write_config([OLD])
        app = self.app()
        self.assertFalse(app.start_folder(NEW))
        self.assertEqual(app.changes.take_settled(), set())

    def test_waiting_flag_survives_a_restart(self):
        app = self.app()
        app.save_config(app.cfg)
        self.assertEqual(self.app().waiting_count(), 1)


class AddFolderTests(unittest.TestCase):
    def test_a_new_folder_waits_instead_of_syncing(self):
        import gui
        win = gui.MainWindow.__new__(gui.MainWindow)
        win.root = None
        win.app = mock.Mock()
        win.app.cfg = {"folders": [OLD]}
        win.app.accounts_info.return_value = [{"email": EMAIL}]
        win.app.folder_account = tray_app.App.folder_account
        win.refresh_folders = lambda: None
        added = {"local": r"D:\EBC", "account": EMAIL, "mode": "two_way"}
        with mock.patch.object(gui, "FolderDialog", return_value=mock.Mock(result=added)):
            win.add_folder()
        saved = win.app.save_config.call_args[0][0]["folders"]
        self.assertEqual(saved, [OLD, {**added, "waiting": True}])
        win.app.sync_now.assert_not_called()


if __name__ == "__main__":
    unittest.main()
