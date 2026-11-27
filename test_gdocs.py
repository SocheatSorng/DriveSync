"""Google Docs/Sheets/Slides as shortcuts on this PC (two-way sync).
Run:  python -m unittest test_gdocs -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gdocs
from drive_sync import DEFAULT_EXCLUDES, Syncer
from test_drive_sync import FakeDrive as UploadFakeDrive
from test_twoway import FakeDrive
from twoway import TwoWaySyncer

SHEET = "application/vnd.google-apps.spreadsheet"


class LocalNameTests(unittest.TestCase):
    def test_names_windows_cannot_store(self):
        self.assertEqual(gdocs.local_name("Micomet "), "Micomet")
        self.assertEqual(gdocs.local_name("Notes..."), "Notes")
        self.assertEqual(gdocs.local_name("a:b?c*.txt"), "a_b_c_.txt")
        self.assertEqual(gdocs.local_name("CON"), "_CON")
        self.assertEqual(gdocs.local_name("nul.txt"), "_nul.txt")
        self.assertEqual(gdocs.local_name("   "), "Untitled")
        self.assertEqual(gdocs.local_name("Console"), "Console")
        self.assertEqual(gdocs.local_name("Photos 2026"), "Photos 2026")


class GDocHelpers(unittest.TestCase):
    def test_names(self):
        item = {"id": "abc_-1", "name": "Plan: term 1?", "mimeType": SHEET}
        self.assertEqual(gdocs.link_name(item), "Plan_ term 1_.gsheet.url")
        self.assertTrue(gdocs.is_link_name("Plan.GSHEET.url"))
        self.assertFalse(gdocs.is_link_name("Plan.url"))
        self.assertEqual(gdocs.drive_name("Plan.gdoc.url"), "Plan")
        self.assertEqual(gdocs.link_name({"id": "x", "name": "S", "mimeType": "application/vnd.google-apps.shortcut"}),
                         "S.glink.url")

    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.gsheet.url"
            gdocs.write_link(p, {"id": "abc_-1", "name": "a", "mimeType": SHEET})
            text = p.read_text()
            self.assertIn("URL=https://docs.google.com/spreadsheets/d/abc_-1/edit", text)
            self.assertEqual(gdocs.read_link(p), "abc_-1")
            p.write_text("[InternetShortcut]\nURL=https://docs.google.com/document/d/XYZ/edit\n")
            self.assertEqual(gdocs.read_link(p), "XYZ")          # e.g. edited by hand
            self.assertIsNone(gdocs.read_link(Path(t) / "missing.gdoc.url"))


class TwoWayGDocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pc = Path(self.tmp.name) / "pc"
        self.bin = Path(self.tmp.name) / "bin"
        self.pc.mkdir()
        self.bin.mkdir()
        self.state = Path(self.tmp.name) / "state.2way.json"
        self.drive = FakeDrive()
        (self.pc / "Script").mkdir()
        (self.pc / "Script/notes.txt").write_text("n")
        self.sync()
        self.doc = self.add_doc("Script", "Enrollment")

    def add_doc(self, folder, name, mime=SHEET):
        parent = self.drive.find(folder) if folder else "root"
        return self.drive._new(name=name, mimeType=mime, parent=parent)

    def sync(self, quick=False):
        self.events = []
        s = TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES, state_path=self.state,
                         recycle=lambda p: shutil.move(p, self.bin / Path(p).name),
                         on_event=lambda k, p, m: self.events.append((k, p)))
        s.run(s.targets_for(drive_ids=self.drive.take_changes(), scan=True) if quick else None)
        return sorted(self.events)

    def alive(self, i):
        return self.drive._alive(self.drive.items[i])

    def test_doc_on_drive_appears_as_a_shortcut(self):
        self.assertEqual(self.sync(), [("downloaded", "Script/Enrollment.gsheet.url")])
        self.assertEqual(gdocs.read_link(self.pc / "Script/Enrollment.gsheet.url"), self.doc)
        self.assertEqual(self.sync(), [])                        # nothing to do next time
        self.assertEqual(self.sync(quick=True), [])

    def test_deleting_the_shortcut_trashes_the_doc(self):
        self.sync()
        (self.pc / "Script/Enrollment.gsheet.url").unlink()
        self.assertEqual(self.sync(quick=True), [("trashed", "Script/Enrollment.gsheet.url")])
        self.assertFalse(self.alive(self.doc))
        self.assertEqual(self.sync(), [])

    def test_deleting_the_folder_trashes_it_with_its_docs(self):
        self.sync()
        shutil.rmtree(self.pc / "Script")
        self.assertEqual(self.sync(quick=True), [("trashed", "Script")])
        self.assertFalse(self.alive(self.doc))
        self.assertFalse((self.pc / "Script").exists())        # and it doesn't come back
        self.assertEqual(self.sync(), [])

    def test_folder_with_a_doc_never_seen_on_this_pc_is_kept(self):
        self.sync()
        shutil.rmtree(self.pc / "Script")
        new = self.add_doc("Script", "Added on Drive just now")
        ev = self.sync(quick=True)
        self.assertIn(("downloaded", "Script/Added on Drive just now.gsheet.url"), ev)
        self.assertIn(("trashed", "Script/Enrollment.gsheet.url"), ev)   # the one you did see goes
        self.assertTrue(self.alive(new))

    def test_renaming_the_shortcut_renames_the_doc(self):
        self.sync()
        (self.pc / "Script/Enrollment.gsheet.url").rename(self.pc / "Script/Enrollment 2026.gsheet.url")
        self.assertEqual(self.sync(quick=True), [("moved", "Script/Enrollment 2026.gsheet.url")])
        self.assertEqual(self.drive.items[self.doc]["name"], "Enrollment 2026")
        self.assertTrue(self.alive(self.doc))
        self.assertEqual(self.sync(), [])

    def test_moving_the_shortcut_moves_the_doc(self):
        self.sync()
        (self.pc / "Archive").mkdir()
        (self.pc / "Script/Enrollment.gsheet.url").rename(self.pc / "Archive/Enrollment.gsheet.url")
        ev = self.sync(quick=True)
        self.assertIn(("moved", "Archive/Enrollment.gsheet.url"), ev)
        self.assertEqual(self.drive.items[self.doc]["parent"], self.drive.find("Archive"))
        self.assertEqual(self.sync(), [])

    def test_moving_out_of_a_deleted_folder_keeps_the_doc(self):
        self.sync()
        (self.pc / "Script/Enrollment.gsheet.url").rename(self.pc / "Enrollment.gsheet.url")
        shutil.rmtree(self.pc / "Script")
        ev = self.sync(quick=True)
        self.assertIn(("moved", "Enrollment.gsheet.url"), ev)
        self.assertIn(("trashed", "Script"), ev)
        self.assertTrue(self.alive(self.doc))
        self.assertEqual(self.drive.items[self.doc]["parent"], "root")

    def test_doc_deleted_or_renamed_on_drive(self):
        self.sync()
        self.drive.items[self.doc]["name"] = "Renamed"
        self.drive._touch(self.doc)
        ev = self.sync(quick=True)
        self.assertEqual(ev, [("deleted_local", "Script/Enrollment.gsheet.url"),
                              ("downloaded", "Script/Renamed.gsheet.url")])
        self.drive.trash(self.doc)
        self.assertEqual(self.sync(quick=True), [("deleted_local", "Script/Renamed.gsheet.url")])
        self.assertFalse((self.pc / "Script/Renamed.gsheet.url").exists())

    def test_copied_shortcut_is_left_alone(self):
        self.sync()
        shutil.copy(self.pc / "Script/Enrollment.gsheet.url", self.pc / "Copy.gsheet.url")
        self.assertEqual(self.sync(), [])
        self.assertTrue(self.alive(self.doc))
        self.assertTrue((self.pc / "Copy.gsheet.url").exists())

    def test_state_from_an_older_version_checks_everything_once(self):
        data = json.loads(self.state.read_text())
        data.pop("links")
        self.state.write_text(json.dumps(data))
        self.drive.take_changes()                               # the Doc isn't in Drive's change list
        self.assertEqual(self.sync(quick=True), [("downloaded", "Script/Enrollment.gsheet.url")])
        self.assertEqual(self.sync(quick=True), [])

    def test_full_check_stopped_half_way_runs_again(self):
        data = json.loads(self.state.read_text())
        data.pop("links")
        self.state.write_text(json.dumps(data))
        self.drive.take_changes()
        with mock.patch.object(TwoWaySyncer, "_settle_moves", side_effect=RuntimeError("app closed")):
            with self.assertRaises(RuntimeError):
                self.sync(quick=True)                           # stopped before the end (state still saved)
        self.assertFalse(json.loads(self.state.read_text()).get("links"))
        self.sync(quick=True)                                   # so the next start checks everything again
        self.assertTrue(json.loads(self.state.read_text())["links"])


class UploadOnlyGDocTests(unittest.TestCase):
    def test_shortcuts_are_not_uploaded(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "src"
            root.mkdir()
            gdocs.write_link(root / "Plan.gdoc.url", {"id": "d1", "name": "Plan",
                                                      "mimeType": "application/vnd.google-apps.document"})
            (root / "a.txt").write_text("a")
            drive = UploadFakeDrive()
            s = Syncer(drive, root, "root", excludes=DEFAULT_EXCLUDES, state_path=Path(t) / "s.json").run()
            self.assertEqual(s["uploaded"], 1)


if __name__ == "__main__":
    unittest.main()
