"""Tests for files renamed or moved on this PC: Drive moves its copy instead of getting a new upload.
Also: Mac leftover files are skipped by default.
Run:  python -m unittest test_moves -v
"""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from drive_sync import DEFAULT_EXCLUDES, Syncer
from test_drive_sync import FakeDrive as UploadFakeDrive
from test_twoway import FakeDrive as TwoWayFakeDrive
from test_twoway import local_tree
from twoway import TwoWaySyncer


class TwoWayMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pc = Path(self.tmp.name) / "pc"
        self.pc.mkdir()
        self.state = Path(self.tmp.name) / "state.2way.json"
        self.drive = TwoWayFakeDrive()
        self.uploads = []
        real = self.drive.upload
        self.drive.upload = lambda path, parent: (self.uploads.append(Path(path).name), real(path, parent))[1]
        self.write("Term 1/lesson.wav", "audio" * 100)
        self.write("Term 1/notes.txt", "notes")
        self.sync()
        self.uploads.clear()

    def write(self, rel, text):
        p = self.pc / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        time.sleep(0.01)
        p.write_text(text)

    def sync(self):
        self.events = []
        return TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                            recycle=lambda p: shutil.rmtree(p) if os.path.isdir(p) else os.remove(p),
                            on_event=lambda k, p, m: self.events.append((k, p))).run()

    def check_same(self):
        self.assertEqual({k: v for k, v in local_tree(self.pc).items()}, self.drive.tree())
        self.assertEqual(self.sync()["uploaded"], 0)   # and a second sync has nothing to do

    def test_rename(self):
        (self.pc / "Term 1/lesson.wav").rename(self.pc / "Term 1/lesson 1.wav")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"], s["trashed"]), (1, 0, 0))
        self.assertEqual(self.uploads, [])
        self.assertIn(("moved", "Term 1/lesson 1.wav"), self.events)
        self.check_same()

    def test_move_into_a_new_folder(self):
        (self.pc / "Archive").mkdir()
        (self.pc / "Term 1/lesson.wav").rename(self.pc / "Archive/lesson.wav")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"]), (1, 0))
        self.check_same()

    def test_folder_rename_moves_its_files(self):
        (self.pc / "Term 1").rename(self.pc / "Term One")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"], s["trashed"]), (2, 0, 1))   # the old, now empty folder
        self.check_same()

    def test_rename_and_edit_uploads(self):
        (self.pc / "Term 1/notes.txt").unlink()
        self.write("Term 1/notes v2.txt", "different notes")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"], s["trashed"]), (0, 1, 1))
        self.check_same()

    def test_new_files_upload_straight_away_not_at_the_end(self):
        self.write("Term 2/new recording.wav", "brand new" * 50)
        (self.pc / "Term 1/lesson.wav").rename(self.pc / "Term 1/lesson 1.wav")
        before_end = []
        real = TwoWaySyncer._settle_moves

        def settle(syncer):
            before_end.extend(self.uploads)
            return real(syncer)
        with mock.patch.object(TwoWaySyncer, "_settle_moves", settle):
            s = self.sync()
        self.assertEqual(before_end, ["new recording.wav"])   # not held back: nothing it could be moved from
        self.assertEqual((s["moved"], s["uploaded"]), (1, 1))
        self.check_same()

    def test_copy_is_uploaded_not_moved(self):
        shutil.copy(self.pc / "Term 1/notes.txt", self.pc / "notes copy.txt")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"]), (0, 1))
        self.check_same()


class BatchDrive(TwoWayFakeDrive):
    """A fake Drive that can list many folders in one request, like the real one."""

    def list_children_of(self, ids):
        self.batches = getattr(self, "batches", 0) + 1
        return [{**c, "parents": [f]} for f in ids for c in self.list_children(f)]


class DeletedFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pc = Path(self.tmp.name) / "pc"
        deep = self.pc / "Project" / ".venv" / "Lib" / "site-packages"
        for i in range(30):                                   # a .venv-like tree: many small folders
            (deep / f"pkg{i}" / "sub").mkdir(parents=True)
            (deep / f"pkg{i}" / "sub" / "m.py").write_text(f"# {i}")
        self.drive = BatchDrive()
        self.state = Path(self.tmp.name) / "s.json"
        self.sync()

    def sync(self):
        return TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                            recycle=lambda p: None).run()

    def test_deleted_folder_is_checked_in_a_few_requests_and_trashed(self):
        shutil.rmtree(self.pc / "Project")
        self.drive.batches = 0
        s = self.sync()
        self.assertEqual(s["trashed"], 1)
        self.assertLessEqual(self.drive.batches, 6)            # one per level, not one per folder (90+)
        self.assertEqual(self.drive.tree(), {})

    def test_deleted_folder_with_something_new_on_drive_is_kept(self):
        shutil.rmtree(self.pc / "Project")
        self.drive.web_add("Project/.venv/Lib/site-packages/pkg7/sub", "new.txt", "added on Drive")
        s = self.sync()
        # The folder stays (it has something new); the files deleted on this PC go to the Drive trash.
        self.assertEqual((s["trashed"], s["downloaded"]), (30, 1))
        self.assertTrue((self.pc / "Project/.venv/Lib/site-packages/pkg7/sub/new.txt").exists())
        self.assertIn("Project/.venv/Lib/site-packages/pkg7/sub/new.txt", self.drive.tree())


class UploadOnlyMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "src"
        (self.root / "Term 1").mkdir(parents=True)
        (self.root / "Term 1/lesson.wav").write_text("audio" * 100)
        self.drive = UploadFakeDrive()
        self.sync(delete=True)

    def sync(self, **kw):
        self.drive.calls.clear()
        return Syncer(self.drive, self.root, "root", excludes=DEFAULT_EXCLUDES,
                      state_path=Path(self.tmp.name) / "s.json", **kw).run()

    def test_rename_with_match_exactly_moves_on_drive(self):
        (self.root / "Term 1").rename(self.root / "Term One")
        s = self.sync(delete=True)
        self.assertEqual((s["moved"], s["uploaded"], s["trashed"]), (1, 0, 1))
        self.assertEqual(set(self.drive.tree()), {"Term One/", "Term One/lesson.wav"})

    def test_without_match_exactly_the_old_copy_stays(self):
        (self.root / "Term 1/lesson.wav").rename(self.root / "Term 1/lesson 1.wav")
        s = self.sync()
        self.assertEqual((s["moved"], s["uploaded"]), (0, 1))
        self.assertEqual(set(self.drive.tree()), {"Term 1/", "Term 1/lesson.wav", "Term 1/lesson 1.wav"})


class MacLeftoverTests(unittest.TestCase):
    def test_mac_leftovers_are_not_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "src"
            (root / "__MACOSX" / "Speaker 02").mkdir(parents=True)
            (root / "__MACOSX" / "Speaker 02" / "._a.wav").write_text("x")
            (root / "._b.wav").write_text("x")
            (root / "b.wav").write_text("audio")
            drive = UploadFakeDrive()
            Syncer(drive, root, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(tmp) / "s.json").run()
            self.assertEqual(set(drive.tree()), {"b.wav"})


if __name__ == "__main__":
    unittest.main()
