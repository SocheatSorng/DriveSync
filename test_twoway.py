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
from unittest import mock

import twoway
from drive_sync import DEFAULT_EXCLUDES, FOLDER_MIME
from twoway import DriveFolderGone, TwoWaySyncer


class FakeDrive:
    """In-memory Google Drive that stores file contents (so downloads work)."""

    def __init__(self):
        self.items = {"root": {"id": "root", "name": "", "mimeType": FOLDER_MIME, "parent": None}}
        self.ids = itertools.count(1)
        self.changes: set[str] = set()   # like Google's change list: changed ids and their folders
        self.listed: list[str] = []      # folders whose contents were asked for

    def _touch(self, i):
        self.changes.add(i)
        if self.items[i].get("parent"):
            self.changes.add(self.items[i]["parent"])

    def take_changes(self):
        got, self.changes = self.changes, set()
        return got

    def _new(self, **kw):
        i = f"id{next(self.ids)}"
        self.items[i] = {"id": i, **kw}
        self._touch(i)
        return i

    def _alive(self, item):
        while item:
            if item.get("trashed"):
                return False
            item = self.items.get(item.get("parent"))
        return True

    def list_children(self, fid):
        self.listed.append(fid)
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
        self._touch(fid)

    def download(self, fid, dest, before_replace=None, **kw):
        if before_replace:
            before_replace()
        Path(dest).write_bytes(self.items[fid]["content"])

    def trash(self, fid):
        self.items[fid]["trashed"] = True
        self._touch(fid)

    # helpers for tests (act like a person using drive.google.com)
    def find(self, path):
        cur = "root"
        for part in path.split("/"):
            cur = next(i["id"] for i in self.items.values()
                       if i.get("parent") == cur and i["name"] == part and self._alive(i))
        return cur

    def web_edit(self, path, text):
        data = text.encode()
        i = self.find(path)
        self.items[i].update(content=data, md5Checksum=hashlib.md5(data).hexdigest())
        self._touch(i)

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

    def test_progress_is_saved_during_a_long_sync(self):
        # If the app is closed or crashes half-way, the next start must not start from nothing.
        for n in "abc":
            self.write(f"{n}.txt", n)
        saved_while_running = []
        real_upload = self.drive.upload

        def upload(path, parent):
            if self.state.exists():
                saved_while_running.append(set(json.loads(self.state.read_text())["base"]))
            return real_upload(path, parent)

        self.drive.upload = upload
        with mock.patch.object(twoway, "SAVE_EVERY", 0):
            self.sync()
        self.assertEqual(saved_while_running, [{"a.txt"}, {"a.txt", "b.txt"}])

    def test_temp_download_names_are_never_synced(self):
        self.start()
        self.write("~drivesync-x.part", "partial")
        self.assertEqual(self.sync(), [])

    # -- saved again while syncing ---------------------------------------------
    def _save_during(self, method, text, drive_gets=None):
        """Make drive.<method> act as if the user saved `text` while it was transferring (and, for
        uploads, as if Drive ended up with `drive_gets` - e.g. a mix of old and new bytes)."""
        real = getattr(self.drive, method)

        def during(fid_or_path, *args, **kw):
            if method == "download":
                self.write(Path(args[0]).relative_to(self.pc).as_posix(), text)
                return real(fid_or_path, *args, **kw)
            result = real(fid_or_path, *args, **kw)
            fid = result if method == "upload" else fid_or_path
            if drive_gets is not None:
                data = drive_gets.encode()
                self.drive.items[fid].update(content=data, md5Checksum=hashlib.md5(data).hexdigest())
            path = Path(args[0] if method == "update" else fid_or_path)
            self.write(path.relative_to(self.pc).as_posix(), text)
            setattr(self.drive, method, real)  # only the first transfer
            return result

        setattr(self.drive, method, during)

    def test_saved_while_updating_is_sent_next_sync(self):
        self.start()
        self.write("notes.txt", "n2")
        self._save_during("update", "n3")
        self.assertEqual(self.sync(), [("updated", "notes.txt")])
        self.assertEqual(self.sync(), [("updated", "notes.txt")])   # the newer save isn't lost
        self.assertEqual(self.drive.tree()["notes.txt"], "n3")
        self.assertEqual(self.sync(), [])

    def test_big_file_saved_mid_upload_does_not_come_back_mixed(self):
        # Big files go up in pieces: Drive can end up with half old, half new bytes.
        self.start()
        self.write("notes.txt", "n2")
        self._save_during("update", "n3", drive_gets="n2-and-n3-mixed")
        self.sync()
        self.assertEqual(self.sync(), [("updated", "notes.txt")])   # PC copy wins, no conflict
        self.assertEqual((self.pc / "notes.txt").read_text(), "n3")
        self.assertEqual(self.drive.tree()["notes.txt"], "n3")

    def test_new_file_saved_while_uploading_is_sent_next_sync(self):
        self.start()
        self.write("new.txt", "v1")
        self._save_during("upload", "v2")
        self.assertEqual(self.sync(), [("uploaded", "new.txt")])
        self.assertEqual(self.sync(), [("updated", "new.txt")])
        self.assertEqual(self.drive.tree()["new.txt"], "v2")

    def test_saved_while_downloading_keeps_the_pc_version(self):
        self.start()
        self.drive.web_edit("notes.txt", "web")
        self._save_during("download", "pc")
        self.assertEqual(self.sync(), [("skipped", "notes.txt")])
        self.assertEqual((self.pc / "notes.txt").read_text(), "pc")   # not overwritten
        ev = self.sync()                                              # now: edited on both sides
        self.assertIn(("conflict", "notes.txt"), ev)
        self.assertEqual((self.pc / "notes.txt").read_text(), "web")
        kept = [v for k, v in local_tree(self.pc).items() if "conflict" in k]
        self.assertEqual(kept, ["pc"])

    def test_created_on_pc_while_new_drive_file_downloads(self):
        self.start()
        self.drive.web_add("", "report.txt", "web")
        self._save_during("download", "pc")
        self.assertEqual(self.sync(), [("skipped", "report.txt")])
        self.assertEqual((self.pc / "report.txt").read_text(), "pc")
        self.assertIn(("conflict", "report.txt"), self.sync())
        self.assertEqual(sorted(v for k, v in local_tree(self.pc).items() if "report" in k), ["pc", "web"])

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


class QuickSyncTests(TwoWayTests):
    """Every scenario above again, but each sync after the first looks only at what changed:
    Drive's change list plus the quick size/time comparison on the PC (what happens at startup)."""

    def sync(self, migrate_from=None):
        self.events = []
        s = TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                         migrate_from=migrate_from, recycle=self.recycle,
                         on_event=lambda k, p, m: self.events.append((k, p)))
        s.run(s.targets_for(drive_ids=self.drive.take_changes(), scan=True))
        return sorted(self.events)

    def quick(self, paths=(), scan=False):
        self.events, self.drive.listed = [], []
        s = TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                         recycle=self.recycle, on_event=lambda k, p, m: self.events.append((k, p)))
        targets = s.targets_for(paths=[str(self.pc / p) for p in paths],
                                drive_ids=self.drive.take_changes(), scan=scan)
        s.run(targets)
        return targets, sorted(self.events)

    def big_tree(self):
        for u in range(1, 6):
            for f in range(3):
                self.write(f"Unit {u}/Week {f}/file{f}.txt", "x")
        self.sync()
        self.drive.take_changes()          # Drive Sync's own uploads (seen at the next poll)

    def test_nothing_changed_checks_no_folders(self):
        self.big_tree()
        targets, ev = self.quick(scan=True)
        self.assertEqual((targets, ev, self.drive.listed), (set(), [], []))

    def test_pc_change_checks_only_its_folder(self):
        self.big_tree()
        self.write("Unit 3/Week 1/file1.txt", "edited")
        targets, ev = self.quick(scan=True)
        self.assertEqual(targets, {"Unit 3/Week 1"})
        self.assertEqual(ev, [("updated", "Unit 3/Week 1/file1.txt")])
        self.assertEqual(self.drive.listed, [self.drive.find("Unit 3/Week 1")])

    def test_drive_change_checks_only_its_folder(self):
        self.big_tree()
        self.drive.web_edit("Unit 2/Week 0/file0.txt", "from web")
        targets, ev = self.quick()
        # Google lists the file's folder as changed too; a changed folder's own folder is checked as
        # well (it may have been renamed or moved) - one extra listing, still nothing else.
        self.assertEqual(targets, {"Unit 2/Week 0", "Unit 2"})
        self.assertEqual(ev, [("downloaded", "Unit 2/Week 0/file0.txt")])
        self.assertEqual((self.pc / "Unit 2/Week 0/file0.txt").read_text(), "from web")

    def test_watched_paths_without_scanning(self):
        self.big_tree()
        self.write("Unit 5/Week 2/new.txt", "n")
        targets, ev = self.quick(paths=["Unit 5/Week 2/new.txt"])
        self.assertEqual((targets, ev), ({"Unit 5/Week 2"}, [("uploaded", "Unit 5/Week 2/new.txt")]))

    def test_new_folder_on_pc_is_uploaded_whole(self):
        self.big_tree()
        self.write("Unit 9/a/b.txt", "deep")
        targets, ev = self.quick(paths=["Unit 9/a/b.txt"])  # its folders aren't known yet: the
        self.assertEqual(targets, {""})                     # nearest known one is checked
        self.assertEqual(ev, [("folder", "Unit 9"), ("folder", "Unit 9/a"), ("uploaded", "Unit 9/a/b.txt")])

    def test_folder_deleted_on_pc_found_by_scan(self):
        self.big_tree()
        shutil.rmtree(self.pc / "Unit 4")
        targets, ev = self.quick(scan=True)
        self.assertEqual(targets, {""})
        self.assertIn(("trashed", "Unit 4"), ev)

    def test_folder_moved_on_drive(self):
        self.big_tree()
        item = self.drive.items[self.drive.find("Unit 1/Week 0")]
        item["parent"] = self.drive.find("Unit 2")           # moved from Unit 1 to Unit 2 on the web
        self.drive.changes |= {item["id"], self.drive.find("Unit 2")}
        targets, ev = self.quick()
        self.assertEqual(targets, {"", "Unit 1", "Unit 2", "Unit 1/Week 0"})
        self.assertFalse((self.pc / "Unit 1/Week 0").exists())
        self.assertEqual((self.pc / "Unit 2/Week 0/file0.txt").read_text(), "x")
        self.assertEqual(local_tree(self.pc), self.drive.tree())

    def test_changes_outside_the_synced_folder_are_ignored(self):
        self.big_tree()
        self.drive.changes |= {"some-other-file", "some-other-folder"}
        targets, ev = self.quick()
        self.assertEqual((targets, ev, self.drive.listed), (set(), [], []))

    def test_upload_only_state_needs_a_full_check(self):
        self.write("a.txt", "a")
        self.state.write_text(json.dumps({"version": 2, "root": "root",
                                          "base": {"Old": {"t": "d"}}}))   # no Drive id yet
        s = TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state)
        self.assertIsNone(s.targets_for(scan=True))


if __name__ == "__main__":
    unittest.main()
