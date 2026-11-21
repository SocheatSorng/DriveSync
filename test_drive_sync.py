"""Tests for the sync logic using an in-memory fake Drive (no network needed).
Run:  python -m unittest test_drive_sync -v
"""
import hashlib
import itertools
import os
import tempfile
import time
import unittest
from pathlib import Path

from drive_sync import DEFAULT_EXCLUDES, FOLDER_MIME, Syncer


class FakeDrive:
    def __init__(self):
        self.items = {"root": {"id": "root", "name": "", "mimeType": FOLDER_MIME, "parent": None}}
        self.ids = itertools.count(1)
        self.calls = []

    def _new(self, **kw):
        i = f"id{next(self.ids)}"
        self.items[i] = {"id": i, **kw}
        return i

    def list_children(self, folder_id):
        if folder_id is None:
            return []
        return [dict(v) for v in self.items.values()
                if v.get("parent") == folder_id and not v.get("trashed")]

    def find_folder(self, name, parent_id):
        return next((c["id"] for c in self.list_children(parent_id)
                     if c["name"] == name and c["mimeType"] == FOLDER_MIME), None)

    def create_folder(self, name, parent_id):
        self.calls.append(("mkdir", name))
        return self._new(name=name, mimeType=FOLDER_MIME, parent=parent_id)

    def upload(self, path, parent_id):
        self.calls.append(("upload", path.name))
        return self._new(name=path.name, mimeType="application/octet-stream", parent=parent_id,
                         md5Checksum=hashlib.md5(path.read_bytes()).hexdigest())

    def update(self, file_id, path):
        self.calls.append(("update", path.name))
        self.items[file_id]["md5Checksum"] = hashlib.md5(path.read_bytes()).hexdigest()

    def trash(self, file_id):
        self.calls.append(("trash", self.items[file_id]["name"]))
        self.items[file_id]["trashed"] = True

    def tree(self, folder="root", prefix=""):
        out = {}
        for c in self.list_children(folder):
            p = prefix + c["name"]
            if c["mimeType"] == FOLDER_MIME:
                out[p + "/"] = None
                out.update(self.tree(c["id"], p + "/"))
            else:
                out[p] = c.get("md5Checksum")
        return out


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "src"
        self.state = Path(self.tmp.name) / "state.json"
        self.root.mkdir()
        (self.root / "a.txt").write_text("hello")
        (self.root / "sub" / "deep").mkdir(parents=True)
        (self.root / "sub" / "b.txt").write_text("world")
        (self.root / "sub" / "deep" / "c.bin").write_bytes(os.urandom(1000))
        (self.root / "empty.txt").write_text("")
        (self.root / "Thumbs.db").write_text("junk")
        self.drive = FakeDrive()

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        self.drive.calls.clear()
        kw.setdefault("excludes", DEFAULT_EXCLUDES)
        return Syncer(self.drive, self.root, "root", state_path=self.state, **kw).run()

    def test_initial_upload_mirrors_tree_and_skips_junk(self):
        s = self.sync()
        self.assertEqual(s["uploaded"], 4)
        self.assertEqual(s["folders"], 2)
        self.assertEqual(set(self.drive.tree()),
                         {"a.txt", "empty.txt", "sub/", "sub/b.txt", "sub/deep/", "sub/deep/c.bin"})

    def test_second_run_is_noop(self):
        self.sync()
        s = self.sync()
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(s["unchanged"], 4)

    def test_modified_file_is_updated_in_place(self):
        self.sync()
        time.sleep(0.01)
        (self.root / "sub" / "b.txt").write_text("changed!")
        s = self.sync()
        self.assertEqual(self.drive.calls, [("update", "b.txt")])
        self.assertEqual(s["updated"], 1)

    def test_delete_trashes_removed_items_only_with_flag(self):
        self.sync()
        (self.root / "a.txt").unlink()
        (self.root / "sub" / "deep" / "c.bin").unlink()
        (self.root / "sub" / "deep").rmdir()
        self.sync()
        self.assertEqual(self.drive.calls, [])  # no --delete: nothing removed
        s = self.sync(delete=True)
        self.assertEqual(sorted(self.drive.calls), [("trash", "a.txt"), ("trash", "deep")])
        self.assertEqual(s["trashed"], 2)
        self.assertEqual(set(self.drive.tree()), {"empty.txt", "sub/", "sub/b.txt"})

    def test_delete_leaves_google_native_files(self):
        self.drive._new(name="Notes", mimeType="application/vnd.google-apps.document", parent="root")
        self.sync(delete=True)
        self.assertIn("Notes", self.drive.tree())

    def test_dry_run_changes_nothing(self):
        s = self.sync(dry_run=True, delete=True)
        self.assertEqual(self.drive.calls, [])
        self.assertEqual(s["uploaded"], 4)
        self.assertFalse(self.state.exists())

    def test_user_excludes(self):
        self.sync(excludes=DEFAULT_EXCLUDES + ["sub"])
        self.assertEqual(set(self.drive.tree()), {"a.txt", "empty.txt"})


if __name__ == "__main__":
    unittest.main()
