"""Tests for the Activity history: what's recorded, summaries, filters, search, storage.
Run:  python -m unittest test_activity -v
"""
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import activity
from drive_sync import DEFAULT_EXCLUDES, Syncer
from test_drive_sync import FakeDrive


def run_with(*items, trigger="changes"):
    r = activity.new_run(r"D:\EBC\Lessons", "me@school.edu", trigger)
    for kind, path, *msg in items:
        activity.add_item(r, kind, path, *msg)
    return r


class EntryTests(unittest.TestCase):
    def test_runs_that_changed_nothing_are_not_kept(self):
        self.assertFalse(activity.is_worth_keeping(run_with()))
        self.assertTrue(activity.is_worth_keeping(run_with(("uploaded", "a.docx"))))
        self.assertTrue(activity.is_worth_keeping(run_with(("error", "b.docx", "locked"))))
        self.assertTrue(activity.is_worth_keeping(activity.new_event("info", "Signed in")))

    def test_summary(self):
        r = run_with(("uploaded", "a"), ("uploaded", "b"), ("updated", "c"), ("error", "d", "x"), ("error", "e", "y"))
        self.assertEqual(activity.summary(r), "2 uploaded · 1 updated · 2 problems")
        self.assertEqual(activity.summary(run_with(("folder", "Unit 2"))), "1 new folder")

    def test_big_runs_are_capped_but_problems_never_dropped(self):
        r = run_with(*[("uploaded", f"f{i}") for i in range(activity.MAX_ITEMS + 50)])
        activity.add_item(r, "error", "late.docx", "locked")
        self.assertEqual(r["counts"]["uploaded"], activity.MAX_ITEMS + 50)
        self.assertEqual(r["more"], 50)
        self.assertEqual(r["items"][-1]["path"], "late.docx")

    def test_filters_and_search(self):
        up = run_with(("uploaded", "Unit 2/quiz.pdf"))
        bad = run_with(("error", "report.docx", "file is locked"))
        ev = activity.new_event("warning", "me@school.edu needs to sign in again")
        self.assertEqual([activity.matches(e, "Changes", "") for e in (up, bad, ev)], [True, False, False])
        self.assertEqual([activity.matches(e, "Problems", "") for e in (up, bad, ev)], [False, True, True])
        self.assertTrue(activity.matches(up, "All", "QUIZ"))           # file name, any case
        self.assertTrue(activity.matches(bad, "All", "locked"))        # error message
        self.assertTrue(activity.matches(up, "All", "lessons"))        # folder
        self.assertTrue(activity.matches(ev, "All", "sign in"))
        self.assertFalse(activity.matches(up, "All", "timetable"))

    def test_day_labels(self):
        now = datetime(2026, 11, 21, 20, 0).timestamp()
        self.assertEqual(activity.day_label(now - 3600, now), "Today")
        self.assertEqual(activity.day_label((datetime(2026, 11, 20, 9, 0)).timestamp(), now), "Yesterday")
        self.assertEqual(activity.day_label((datetime(2026, 11, 18, 9, 0)).timestamp(), now), "Wednesday, 18 November")
        self.assertEqual(activity.day_label((datetime(2025, 3, 1)).timestamp(), now), "01 March 2025")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "activity.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_across_restarts_and_trimmed(self):
        store = activity.ActivityStore(self.path, keep=10)
        for i in range(25):
            store.append(activity.new_event("info", f"event {i}"))
        loaded = activity.ActivityStore(self.path, keep=10).load()   # "restart"
        self.assertEqual(len(loaded), 10)
        self.assertEqual(loaded[-1]["message"], "event 24")
        self.assertLessEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 20)

    def test_half_written_line_is_ignored(self):
        store = activity.ActivityStore(self.path)
        store.append(activity.new_event("info", "ok"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"type": "run", "ts": 1')  # crash mid-write
        self.assertEqual([e["message"] for e in store.load()], ["ok"])

    def test_clear(self):
        store = activity.ActivityStore(self.path)
        store.append(activity.new_event("info", "x"))
        store.clear()
        self.assertEqual(store.load(), [])


class SyncEventTests(unittest.TestCase):
    """The sync engine reports each action, with paths relative to the synced folder."""

    def test_events(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "src"
            (root / "Unit 2").mkdir(parents=True)
            (root / "a.txt").write_text("1")
            (root / "Unit 2" / "quiz.pdf").write_text("q")
            drive = FakeDrive()
            got = []

            def sync(**kw):
                Syncer(drive, root, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(d) / "s.json",
                       on_event=lambda k, p, m: got.append((k, p)), **kw).run()

            sync()
            self.assertEqual(sorted(got), [("folder", "Unit 2"), ("uploaded", "Unit 2/quiz.pdf"),
                                           ("uploaded", "a.txt")])
            got.clear()
            time.sleep(0.01)
            (root / "a.txt").write_text("2")
            (root / "Unit 2" / "quiz.pdf").unlink()
            sync(delete=True)
            self.assertEqual(sorted(got), [("trashed", "Unit 2/quiz.pdf"), ("updated", "a.txt")])


if __name__ == "__main__":
    unittest.main()
