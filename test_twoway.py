"""Tests for two-way sync: every case of what changed where, plus the safety rules.
Run:  python -m unittest test_twoway -v
"""
import hashlib
import itertools
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from drive_sync import DEFAULT_EXCLUDES, FOLDER_MIME
from twoway import DriveFolderGone, TwoWaySyncer


class FakeDrive:
    """In-memory Google Drive that stores file contents (so downloads work)."""

    def __init__(self):
        self.items = {"root": {"id": "root", "name": "", "mimeType": FOLDER_MIME, "parent": None}}
        self.ids = itertools.count(1)

    def _new(self, **kw):
        i = f"id{next(self.ids)}"
        self.items[i] = {"id": i, **kw}
        return i

    def _alive(self, item):
        while item:
            if item.get("trashed"):
                return False
            item = self.items.get(item.get("parent"))
        return True

    def list_children(self, fid):
        return [{k: v for k, v in i.items() if k != "content"} for i in self.items.values()
                if i.get("parent") == fid and self._alive(i)]

    def folder_status(self, fid):
        i = self.items.get(fid)
        return "missing" if not i else "ok" if self._alive(i) else "trashed"

    def create_folder(self, name, parent):
        return self._new(name=name, mimeType=FOLDER_MIME, parent=parent)

    def upload(self, path, parent):
        data = Path(path).read_bytes()
        return self._new(name=Path(path).name, mimeType="application/octet-stream", parent=parent,
                         content=data, md5Checksum=hashlib.md5(data).hexdigest())

    def update(self, fid, path):
        data = Path(path).read_bytes()
        self.items[fid].update(content=data, md5Checksum=hashlib.md5(data).hexdigest())

    def download(self, fid, dest):
        Path(dest).write_bytes(self.items[fid]["content"])

    def trash(self, fid):
        self.items[fid]["trashed"] = True

    # helpers for tests (act like a person using drive.google.com)
    def find(self, path):
        cur = "root"
        for part in path.split("/"):
            cur = next(i["id"] for i in self.items.values()
                       if i.get("parent") == cur and i["name"] == part and self._alive(i))
        return cur

    def web_edit(self, path, text):
        data = text.encode()
        self.items[self.find(path)].update(content=data, md5Checksum=hashlib.md5(data).hexdigest())

    def web_add(self, parent_path, name, text):
        parent = self.find(parent_path) if parent_path else "root"
        data = text.encode()
        return self._new(name=name, mimeType="text/plain", parent=parent, content=data,
                         md5Checksum=hashlib.md5(data).hexdigest())

    def tree(self, fid="root", prefix=""):
        out = {}
        for c in self.list_children(fid):
            p = prefix + c["name"]
            if c["mimeType"] == FOLDER_MIME:
                out[p + "/"] = None
                out.update(self.tree(c["id"], p + "/"))
            else:
                out[p] = self.items[c["id"]].get("content", b"").decode() if "md5Checksum" in c else "<gdoc>"
        return out


def local_tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel + "/" if p.is_dir() else rel] = None if p.is_dir() else p.read_text()
    return out


class TwoWayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.pc = base / "pc"
        self.bin = base / "RecycleBin"
        self.state = base / "state.2way.json"
        self.pc.mkdir()
        self.bin.mkdir()
        self.drive = FakeDrive()
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def recycle(self, path):
        shutil.move(path, self.bin / Path(path).name)

    def sync(self, migrate_from=None):
        self.events = []
        TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                     migrate_from=migrate_from, recycle=self.recycle,
                     on_event=lambda k, p, m: self.events.append((k, p))).run()
        return sorted(self.events)

    def write(self, rel, text):
        p = self.pc / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        time.sleep(0.01)  # make sure the modified time changes
        p.write_text(text)

    def start(self):
        """PC and Drive in sync with: notes.txt, Unit 1/plan.docx, Unit 1/quiz.pdf"""
        self.write("notes.txt", "n1")
        self.write("Unit 1/plan.docx", "p1")
        self.write("Unit 1/quiz.pdf", "q1")
        self.sync()

    # -- first sync ---------------------------------------------------------
    def test_first_sync_merges_both_sides_without_deleting(self):
        self.write("pc-only.txt", "a")
        self.write("same.txt", "s")
        self.drive.web_add("", "drive-only.txt", "b")
        self.drive.web_add("", "same.txt", "s")
        ev = self.sync()
        self.assertEqual(ev, [("downloaded", "drive-only.txt"), ("uploaded", "pc-only.txt")])
        self.assertEqual(local_tree(self.pc), self.drive.tree())

    def test_second_sync_does_nothing(self):
        self.start()
        self.assertEqual(self.sync(), [])

    # -- edits ----------------------------------------------------------------
    def test_edit_on_pc_uploads(self):
        self.start()
        self.write("notes.txt", "n2")
        self.assertEqual(self.sync(), [("updated", "notes.txt")])
        self.assertEqual(self.drive.tree()["notes.txt"], "n2")

    def test_edit_on_drive_downloads(self):
        self.start()
        self.drive.web_edit("Unit 1/plan.docx", "p2")
        self.assertEqual(self.sync(), [("downloaded", "Unit 1/plan.docx")])
        self.assertEqual((self.pc / "Unit 1/plan.docx").read_text(), "p2")

    def test_new_on_drive_downloads_including_folders(self):
        self.start()
        self.drive.web_add("", "Unit 2", "")  # placeholder replaced below
        self.drive.items[self.drive.find("Unit 2")]["mimeType"] = FOLDER_MIME
        self.drive.web_add("Unit 2", "test.pdf", "t")
        ev = self.sync()
        self.assertEqual(ev, [("downloaded", "Unit 2/test.pdf"), ("folder", "Unit 2")])
        self.assertEqual((self.pc / "Unit 2/test.pdf").read_text(), "t")

    def test_edited_on_both_sides_keeps_both(self):
        self.start()
        self.write("notes.txt", "pc version")
        self.drive.web_edit("notes.txt", "drive version")
        ev = self.sync()
        self.assertEqual([k for k, _ in ev], ["conflict"])
        files = {k: v for k, v in local_tree(self.pc).items() if k.startswith("notes")}
        self.assertEqual(files["notes.txt"], "drive version")
        conflict = [k for k in files if "conflict" in k]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(files[conflict[0]], "pc version")
        self.assertEqual(local_tree(self.pc), self.drive.tree())     # both copies on both sides
        self.assertEqual(self.sync(), [])                           # and it settles

    # -- deletions ------------------------------------------------------------
    def test_file_deleted_on_drive_goes_to_recycle_bin(self):
        self.start()
        self.drive.trash(self.drive.find("notes.txt"))
        self.assertEqual(self.sync(), [("deleted_local", "notes.txt")])
        self.assertFalse((self.pc / "notes.txt").exists())
        self.assertEqual((self.bin / "notes.txt").read_text(), "n1")   # recoverable

    def test_folder_deleted_on_drive_goes_to_recycle_bin(self):
        """The reported bug: delete a folder on Google Drive -> it disappears from the PC."""
        self.start()
        self.drive.trash(self.drive.find("Unit 1"))
        self.assertEqual(self.sync(), [("deleted_local", "Unit 1")])
        self.assertFalse((self.pc / "Unit 1").exists())
        self.assertEqual(sorted(p.name for p in (self.bin / "Unit 1").iterdir()), ["plan.docx", "quiz.pdf"])
        self.assertEqual(self.sync(), [])
        self.assertNotIn("Unit 1/", self.drive.tree())  # not re-uploaded

    def test_deleted_on_drive_but_new_file_on_pc_keeps_new_file(self):
        self.start()
        self.drive.trash(self.drive.find("Unit 1"))
        self.write("Unit 1/new homework.docx", "h")
        ev = self.sync()
        self.assertIn(("uploaded", "Unit 1/new homework.docx"), ev)
        self.assertTrue((self.pc / "Unit 1/new homework.docx").exists())
        self.assertFalse((self.pc / "Unit 1/plan.docx").exists())     # the old ones follow the deletion
        self.assertEqual(local_tree(self.pc), self.drive.tree())

    def test_deleted_on_drive_but_edited_on_pc_is_kept(self):
        self.start()
        self.drive.trash(self.drive.find("notes.txt"))
        self.write("notes.txt", "edited after")
        self.assertEqual(self.sync(), [("uploaded", "notes.txt")])
        self.assertEqual(self.drive.tree()["notes.txt"], "edited after")

    def test_deleted_on_pc_goes_to_drive_trash(self):
        self.start()
        shutil.rmtree(self.pc / "Unit 1")
        (self.pc / "notes.txt").unlink()
        self.assertEqual(self.sync(), [("trashed", "Unit 1"), ("trashed", "notes.txt")])
        self.assertEqual(self.drive.tree(), {})

    def test_deleted_on_pc_but_edited_on_drive_is_kept(self):
        self.start()
        (self.pc / "notes.txt").unlink()
        self.drive.web_edit("notes.txt", "edited on web")
        self.assertEqual(self.sync(), [("downloaded", "notes.txt")])
        self.assertEqual((self.pc / "notes.txt").read_text(), "edited on web")

    # -- safety -----------------------------------------------------------------
    def test_trashed_drive_folder_never_empties_the_pc(self):
        self.start()
        for i in list(self.drive.items.values()):
            if i.get("parent") == "root":
                i["trashed"] = True
        self.drive.items["root"]["trashed"] = True       # the whole synced folder was deleted
        with self.assertRaises(DriveFolderGone):
            self.sync()
        self.assertEqual(len(local_tree(self.pc)), 4)     # nothing touched
        self.assertEqual(list(self.bin.iterdir()), [])

    def test_google_docs_are_left_alone(self):
        self.start()
        self.drive.web_add("Unit 1", "Lesson notes", "")
        self.drive.items[self.drive.find("Unit 1/Lesson notes")].pop("md5Checksum")
        self.drive.items[self.drive.find("Unit 1/Lesson notes")]["mimeType"] = "application/vnd.google-apps.document"
        self.assertEqual(self.sync(), [])                 # not downloaded
        shutil.rmtree(self.pc / "Unit 1")                 # folder deleted on PC...
        ev = self.sync()
        self.assertIn("Unit 1/Lesson notes", self.drive.tree())  # ...but the Google Doc isn't trashed
        self.assertIn(("trashed", "Unit 1/plan.docx"), ev)

    def test_temp_download_names_are_never_synced(self):
        self.start()
        self.write("~drivesync-x.part", "partial")
        self.assertEqual(self.sync(), [])

    # -- upgrade from upload-only -----------------------------------------------
    def test_switching_from_upload_only_respects_earlier_syncs(self):
        # Upload-only sync had copied these to Drive (its state = checksum cache).
        self.write("Testing/a.txt", "a")
        self.write("notes.txt", "n")
        for rel in ("Testing/a.txt", "notes.txt"):
            self.drive.upload(self.pc / rel, "root" if "/" not in rel else
                              self.drive.find("Testing") if "Testing" in self.drive.tree() or
                              self.drive.create_folder("Testing", "root") else None)
        old = self.pc.parent / "state.json"
        cache = {}
        for rel in ("Testing/a.txt", "notes.txt"):
            st = (self.pc / rel).stat()
            cache[rel.replace("/", os.sep)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                               "md5": hashlib.md5((self.pc / rel).read_bytes()).hexdigest()}
        old.write_text(json.dumps(cache))
        os.utime(old, (time.time() + 1, time.time() + 1))   # last sync happened after these existed
        # Then: the user deletes "Testing" on Drive, and later creates a new folder on the PC.
        self.drive.trash(self.drive.find("Testing"))
        time.sleep(1.2)
        self.write("Later/new.txt", "x")
        ev = self.sync(migrate_from=old)
        self.assertIn(("deleted_local", "Testing"), ev)    # follows the Drive deletion
        self.assertIn(("uploaded", "Later/new.txt"), ev)   # new since then: uploaded, not deleted
        self.assertFalse((self.pc / "Testing").exists())


if __name__ == "__main__":
    unittest.main()
