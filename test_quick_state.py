"""Tests for what the app remembers between runs so a restart only checks what changed.
Run:  python -m unittest test_quick_state -v
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tray_app
from watcher import ChangeTracker

EMAIL = "teacher@example.com"
FOLDER = {"local": r"C:\School", "account": EMAIL, "mode": "two_way"}


class FakeDrive:
    def __init__(self):
        self.feed: list[set[str]] = []       # change batches Google has, oldest first
        self.started = 0

    def changes_start_token(self):
        self.started += 1
        return str(len(self.feed))

    def changes_since(self, token):
        if token == "expired":
            raise _HttpError(404)
        ids = set().union(*self.feed[int(token):])
        return ids, str(len(self.feed))


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


def new_app(state_path: Path, drive: FakeDrive):
    """An App with just the parts the sync bookkeeping uses (no window, no tray icon)."""
    app = tray_app.App.__new__(tray_app.App)
    with mock.patch.object(tray_app, "SYNC_STATE_PATH", state_path):
        app.sync_state = app._load_sync_state()
    app.change_tokens = app.sync_state["tokens"]
    app.changes = ChangeTracker()
    app.drive_marked = set()
    app.needs_signin = set()
    app.drives = {EMAIL: drive}
    app.load_config = lambda: {"folders": [FOLDER]}
    return app


class QuickStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "sync_state.json"
        self.drive = FakeDrive()
        p1 = mock.patch.object(tray_app, "SYNC_STATE_PATH", self.state)
        p2 = mock.patch.object(tray_app.accounts, "list_accounts", lambda: [EMAIL])
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.addCleanup(self.tmp.cleanup)
        self.key = tray_app.App.folder_key(FOLDER, [EMAIL])

    def app(self):
        return new_app(self.state, self.drive)

    def plan(self, app, full=False, scan=True):
        return app._quick_plan(FOLDER, self.key, EMAIL, full, scan)

    def test_first_start_checks_everything_then_quick(self):
        app = self.app()
        app._poll_drive()                                    # starts Google's change list
        self.assertIsNone(self.plan(app))                    # never fully checked: check it all
        app._synced(self.key, None, {"scope": "full"}, time.time())
        self.assertIsNotNone(self.plan(app))                 # from now on: only what changed

    def test_changes_while_off_are_found_after_restart(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        # App closed. Meanwhile someone edits a file on drive.google.com:
        self.drive.feed.append({"file-1", "folder-A"})
        app = self.app()                                     # next start
        app._poll_drive()
        plan = self.plan(app)
        self.assertEqual(plan["ids"], {"file-1", "folder-A"})
        self.assertEqual(self.drive.started, 1)              # kept its place in the change list

    def test_changes_survive_a_crash_before_syncing(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        self.drive.feed.append({"file-1"})
        app._poll_drive()                                    # seen and saved, then the app is killed
        app = self.app()
        app._poll_drive()                                    # nothing new on Drive now
        self.assertEqual(self.plan(app)["ids"], {"file-1"})  # still to do

    def test_handled_changes_are_forgotten_but_failed_ones_kept(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        self.drive.feed.append({"file-1"})
        app._poll_drive()
        plan = self.plan(app)
        app._synced(self.key, plan, {"scope": "quick", "errors": 1}, time.time())
        self.assertEqual(self.plan(app)["ids"], {"file-1"})  # failed: looked at again
        app._synced(self.key, plan, {"scope": "quick"}, time.time())
        self.assertEqual(self.plan(app)["ids"], set())

    def test_full_check_once_a_day_and_on_sync_now(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time() - 25 * 3600)
        self.assertIsNone(self.plan(app))                    # a day old: check everything
        app._synced(self.key, None, {"scope": "full"}, time.time())
        self.assertIsNotNone(self.plan(app))
        self.assertIsNone(self.plan(app, full=True))         # "Sync now"

    def test_lost_place_in_change_list_means_full_check(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        app.change_tokens[EMAIL] = "expired"                 # e.g. off for a very long time
        app._poll_drive()
        self.assertIsNone(self.plan(app))
        self.assertEqual(self.drive.started, 2)

    def test_upload_only_folders_always_check_everything(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        self.assertIsNone(app._quick_plan(dict(FOLDER, mode="upload"), self.key, EMAIL, False, True))

    def test_pc_paths_from_the_watcher_are_used_once(self):
        app = self.app()
        app._poll_drive()
        app._synced(self.key, None, {"scope": "full"}, time.time())
        app.changes.mark(self.key, paths=[r"C:\School\a.txt"])
        self.assertEqual(self.plan(app, scan=False)["paths"], {r"C:\School\a.txt"})
        self.assertEqual(self.plan(app, scan=False)["paths"], set())


if __name__ == "__main__":
    unittest.main()
