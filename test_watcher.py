"""Tests for instant sync: exclusions, settle timing, and real Windows change notifications.
Run:  python -m unittest test_watcher -v
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from watcher import ChangeTracker, FolderWatcher, is_excluded

EXCLUDES = ["~$*", "*.tmp", "Thumbs.db", "node_modules"]


class ExcludeTests(unittest.TestCase):
    def test_rules(self):
        root = os.path.normpath("C:/data")
        j = lambda *p: os.path.join(root, *p)  # noqa: E731
        self.assertTrue(is_excluded(j("~$Report.docx"), root, EXCLUDES))         # Word lock file
        self.assertTrue(is_excluded(j("sub", "x.tmp"), root, EXCLUDES))
        self.assertTrue(is_excluded(j("app", "node_modules", "a", "b.js"), root, EXCLUDES))
        self.assertFalse(is_excluded(j("Report.docx"), root, EXCLUDES))
        self.assertFalse(is_excluded(j("sub", "notes.txt"), root, EXCLUDES))


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class ChangeTrackerTests(unittest.TestCase):
    def test_waits_until_folder_is_quiet(self):
        clock = FakeClock()
        tr = ChangeTracker(settle=4, clock=clock)
        tr.mark("A")
        self.assertEqual(tr.next_due(), 104)
        clock.t = 102
        tr.mark("A")                       # still being written: timer restarts
        clock.t = 105
        self.assertEqual(tr.take_settled(), set())
        clock.t = 106
        self.assertEqual(tr.take_settled(), {"A"})
        self.assertEqual(tr.take_settled(), set())  # handed out once
        self.assertIsNone(tr.next_due())

    def test_drive_changes_use_a_short_wait_but_never_cut_a_local_one(self):
        clock = FakeClock()
        tr = ChangeTracker(settle=4, clock=clock)
        tr.mark("A", settle=1)                 # Drive-side change: quick
        self.assertEqual(tr.next_due(), 101)
        clock.t = 101
        self.assertEqual(tr.take_settled(), {"A"})
        tr.mark("B")                           # file being saved on this PC (due 105)...
        clock.t = 102
        tr.mark("B", settle=1)                 # ...and Drive changed too: still wait for the save
        clock.t = 104
        self.assertEqual(tr.take_settled(), set())
        clock.t = 105
        self.assertEqual(tr.take_settled(), {"B"})

    def test_folders_settle_independently(self):
        clock = FakeClock()
        tr = ChangeTracker(settle=4, clock=clock)
        tr.mark("A")
        clock.t = 103
        tr.mark("B")
        clock.t = 104
        self.assertEqual(tr.take_settled(), {"A"})
        clock.t = 107
        self.assertEqual(tr.take_settled(), {"B"})


class LiveWatchTests(unittest.TestCase):
    """Uses the real Windows change notifications on a temporary folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "synced"
        self.root.mkdir()
        self.app_dir = self.root / "app-data"   # e.g. the app's own logs inside a synced folder
        self.app_dir.mkdir()
        self.events: list = []
        self.got = threading.Event()

        def on_change(key):
            self.events.append(key)
            self.got.set()

        self.w = FolderWatcher(on_change, ignore_under=(str(self.app_dir),))
        self.w.update({"K": (str(self.root), EXCLUDES)})
        time.sleep(0.3)

    def tearDown(self):
        self.w.stop()
        time.sleep(0.2)
        self.tmp.cleanup()

    def expect(self, action, should_fire: bool):
        self.got.clear()
        self.events.clear()
        action()
        fired = self.got.wait(2.0)
        self.assertEqual(fired, should_fire, f"events: {self.events}")
        time.sleep(1.5)  # Windows delivers events ~1s late; let this action's events drain

    def test_reports_real_changes(self):
        f = self.root / "lesson.docx"
        self.expect(lambda: f.write_text("v1"), True)                       # created
        self.expect(lambda: f.write_text("v2"), True)                       # modified
        self.expect(lambda: f.rename(self.root / "lesson-final.docx"), True)  # renamed
        (self.root / "sub").mkdir()
        time.sleep(0.3)
        self.expect(lambda: (self.root / "sub" / "a.txt").write_text("x"), True)  # inside a subfolder
        self.expect(lambda: (self.root / "lesson-final.docx").unlink(), True)     # deleted
        self.assertEqual(set(self.events) | {"K"}, {"K"})

    def test_ignores_excluded_and_own_files(self):
        self.expect(lambda: (self.root / "~$lesson.docx").write_text("lock"), False)
        self.expect(lambda: (self.root / "download.tmp").write_text("x"), False)
        self.expect(lambda: (self.app_dir / "drive_sync.log").write_text("log line"), False)

    def test_reading_a_file_is_not_a_change(self):
        f = self.root / "read-me.txt"
        self.expect(lambda: f.write_text("hello"), True)
        self.expect(lambda: f.read_text(), False)
        self.expect(lambda: f.read_bytes(), False)

    def test_touching_attributes_is_not_a_change(self):
        f = self.root / "report.docx"
        self.expect(lambda: f.write_text("v1"), True)
        st = f.stat()
        # Same content, but Windows reports "modified" (e.g. last-access / attribute updates).
        self.expect(lambda: os.utime(f, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns)), False)
        self.expect(lambda: f.write_text("v2 - longer"), True)

    def test_unwatching(self):
        self.w.update({})
        self.assertEqual(self.w.watched, 0)
        self.expect(lambda: (self.root / "after.txt").write_text("x"), False)


if __name__ == "__main__":
    unittest.main()
