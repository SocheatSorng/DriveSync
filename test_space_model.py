"""Tests for the Space tab's logic (space_model.py): scanning, selection, and moving items to the
Recycle Bin / Drive trash. Uses temporary files and fake Drive objects only.
Run:  python -m unittest test_space_model -v
"""
import os
import queue
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import space
import space_model as m
from space import DuplicateGroup, Item, Report


def app():
    return types.SimpleNamespace(ui=queue.Queue(), cfg={"folders": []})


def pc_report(tmp: Path) -> Report:
    """junk.tmp (junk), a duplicate pair (keep a, b is an extra copy), and big.iso (biggest file)."""
    paths = {}
    for name, size in (("junk.tmp", 10), ("a.jpg", 100), ("b.jpg", 100), ("big.iso", 5000)):
        p = tmp / name
        p.write_bytes(b"x" * size)
        paths[name] = str(p)
    it = {n: Item(p, p, os.path.getsize(p)) for n, p in paths.items()}
    return Report("pc", str(tmp), total=5210, files=4, junk=[it["junk.tmp"]], biggest_files=[it["big.iso"]],
                  duplicates=[DuplicateGroup(100, [it["a.jpg"], it["b.jpg"]])],
                  biggest_folders=[Item(str(tmp), str(tmp), 5210, is_dir=True)])


class ScanTargetTests(unittest.TestCase):
    def test_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"folders": [{"local": tmp}, {"local": os.path.join(tmp, "gone")}]}
            self.assertEqual(m.scan_target(m.SYNCED, cfg), ("pc", [tmp]))
            self.assertIsNone(m.scan_target(m.SYNCED, {"folders": []}))
            self.assertIsNone(m.scan_target(m.PICK, cfg))
            self.assertEqual(m.scan_target(m.PICK, cfg, tmp), ("pc", [os.path.normpath(tmp)]))
            self.assertEqual(m.scan_target(m.DRIVE + "a@example.com", cfg), ("drive", "a@example.com"))

    def test_sources_leave_out_accounts_that_must_sign_in(self):
        accounts = [{"email": "a@x.com", "needs_signin": False}, {"email": "b@x.com", "needs_signin": True}]
        self.assertEqual(m.sources(accounts), [m.SYNCED, m.PICK, m.DRIVE + "a@x.com"])

    def test_run_scan_selects_what_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            r = pc_report(Path(d))
            a, st = app(), m.SpaceState()
            m.start_scan(st)
            self.assertTrue(st.busy)
            with mock.patch.object(space, "scan_pc", return_value=r):
                m.run_scan(a, st, ("pc", [d]))
            self.assertFalse(st.busy)
            self.assertIs(st.report, r)
            self.assertEqual(st.selected, {it.key for it in r.safe()})
            self.assertEqual(a.ui.get_nowait(), ("space",))

    def test_run_scan_failure_is_reported(self):
        a, st = app(), m.SpaceState()
        with mock.patch.object(space, "scan_pc", side_effect=OSError("disk gone")):
            m.run_scan(a, st, ("pc", ["X:\\"]))
        self.assertEqual(st.status, "Couldn't finish: disk gone")
        self.assertFalse(st.busy)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.st = m.SpaceState()
        self.st.report = pc_report(self.tmp)
        self.st.selected = {it.key for it in self.st.report.safe()}

    def test_button_and_selection(self):
        self.assertEqual(m.selection(self.st), (2, 110))
        self.assertEqual(m.delete_button(self.st), ("Move 2 selected (110 B) to Recycle Bin", True))
        self.st.selected.clear()
        self.assertEqual(m.delete_button(self.st), ("Nothing selected", False))
        self.st.busy, self.st.progress = True, "Moving 1 of 2…"
        self.assertEqual(m.delete_button(self.st), ("Moving 1 of 2…", False))

    def test_question_warns_when_every_copy_is_selected(self):
        q, warn = m.delete_question(self.st)
        self.assertFalse(warn)
        self.assertIn("Move 2 item(s) (110 B) to the Recycle Bin?", q)
        self.st.selected.add(str(self.tmp / "a.jpg"))
        q, warn = m.delete_question(self.st)
        self.assertTrue(warn)
        self.assertIn("All copies of 1 duplicate file(s) are selected", q)

    def test_texts(self):
        s = m.summary(self.st.report)
        self.assertEqual(s.title, "This PC")
        self.assertIn("4 files", s.text)
        self.assertIn("Selected items go to the Recycle Bin", s.note)
        self.assertIsNone(s.trash_button)
        rows = [label for _it, label in m.safe_rows(self.st.report)]
        self.assertTrue(any("(junk)" in x for x in rows))
        self.assertTrue(any("copy of" in x for x in rows))
        self.assertIsNone(m.more_text(5))
        self.assertEqual(m.more_text(130), "…and 30 more items (the biggest are shown)")


class DeleteTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.app = app()
        self.st = m.SpaceState()
        self.st.report = pc_report(self.tmp)

    def test_pc_items_go_to_the_recycle_bin_and_leave_the_report(self):
        st = self.st
        st.selected = {str(self.tmp / "junk.tmp"), str(self.tmp / "b.jpg")}
        keys = m.start_delete(st)
        self.assertTrue(st.busy)
        binned = []
        with mock.patch("send2trash.send2trash", side_effect=lambda p: (binned.append(p), os.remove(p))):
            m.run_delete(self.app, st, keys)
        self.assertEqual(sorted(binned), sorted(keys))                # sent to the bin, never deleted outright
        self.assertFalse(st.busy)
        self.assertEqual(st.selected, set())
        self.assertEqual(st.report.junk, [])
        self.assertEqual(st.report.duplicates, [])                    # one copy left: no longer a duplicate
        self.assertEqual(st.report.total, 5210 - 110)
        self.assertEqual(st.status, "Moved 2 item(s) (110 B) to the Recycle Bin.")
        self.assertIn(("space",), list(self.app.ui.queue))

    def test_missing_items_count_as_done_and_failures_are_reported(self):
        st = self.st
        os.remove(self.tmp / "junk.tmp")                              # already gone
        st.selected = {str(self.tmp / "junk.tmp"), str(self.tmp / "big.iso")}
        keys = m.start_delete(st)
        with mock.patch("send2trash.send2trash", side_effect=PermissionError("in use")) as s2t:
            m.run_delete(self.app, st, keys)
        s2t.assert_called_once_with(str(self.tmp / "big.iso"))
        self.assertTrue((self.tmp / "big.iso").exists())
        self.assertIn("Moved 1 item(s)", st.status)
        self.assertIn("1 couldn't be moved", st.status)
        self.assertEqual(st.selected, {str(self.tmp / "big.iso")})

    def test_drive_items_go_to_the_drive_trash(self):
        st = self.st
        it = Item("fileid1", "Photos/x.jpg", 300, extra={"used": 300})
        st.report = Report("drive", "a@example.com", total=300, files=1, junk=[it])
        st.email = "a@example.com"
        st.selected = {"fileid1"}
        drive = mock.Mock()
        self.app.drive_for_browsing = mock.Mock(return_value=drive)
        m.run_delete(self.app, st, m.start_delete(st))
        self.app.drive_for_browsing.assert_called_once_with("a@example.com")
        drive.trash.assert_called_once_with("fileid1")
        self.assertEqual(st.status, "Moved 1 item(s) (300 B) to the Drive trash.")

    def test_old_versions(self):
        st = self.st
        a, b = Item("f1", "Doc.pdf", 1000), Item("f2", "Big.mov", 5000)
        st.report = Report("drive", "a@example.com", old_versions=[a, b])
        st.email, st.old_selected = "a@example.com", {"f1", "f2"}
        self.assertEqual(m.old_button(st), ("Remove old versions of 2 files", True))
        items, q = m.old_question(st)
        self.assertEqual([i.key for i in items], ["f1", "f2"])
        self.assertIn("can't be restored", q)
        self.app.drive_for_browsing = mock.Mock(return_value=mock.Mock(s="service"))
        m.start_old(st)
        with mock.patch.object(space, "delete_old_versions", side_effect=[2, RuntimeError("no access")]):
            m.run_old(self.app, st, items)
        self.assertEqual([i.key for i in st.report.old_versions], ["f2"])
        self.assertEqual(st.old_selected, {"f2"})
        self.assertIn("Removed old versions of 1 file(s)", st.status)
        self.assertIn("1 couldn't be changed", st.status)
        self.assertFalse(st.busy)


class OpenTargetTests(unittest.TestCase):
    def test_pc_and_drive(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.txt"
            f.write_text("x")
            r = Report("pc", d)
            self.assertEqual(m.open_target(r, Item(str(f), str(f), 1)), ("folder", d))
            self.assertEqual(m.open_target(r, Item("Z:\\gone\\x", "Z:\\gone\\x", 1)), ("folder", None))
        r = Report("drive", "a@example.com")
        self.assertEqual(m.open_target(r, Item("fid", "x", 1, extra={"parent": "p1"})),
                         ("url", "https://drive.google.com/drive/folders/p1"))
        self.assertEqual(m.open_target(r, Item("dir1", "D", 1, is_dir=True)),
                         ("url", "https://drive.google.com/drive/folders/dir1"))


if __name__ == "__main__":
    unittest.main()
