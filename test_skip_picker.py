import tempfile
import types
import unittest
from pathlib import Path

from drive_sync import DEFAULT_EXCLUDES, FOLDER_MIME
from skip_picker import pattern_for, split_patterns
from test_twoway import FakeDrive as TwoWayFakeDrive
from test_twoway import local_tree
from twoway import TwoWaySyncer


def skipped(patterns, rel):
    """Ask the real sync code whether this path is skipped."""
    fake = types.SimpleNamespace(excludes=patterns)
    return TwoWaySyncer._excluded(fake, rel.rsplit("/", 1)[-1], rel)


class PatternTests(unittest.TestCase):
    def test_a_picked_path_skips_only_that_item(self):
        for rel in ["Project/.venv", "Photos [2024]", "Notes, old.txt", "a*b?/c.txt", "Plain"]:
            p = pattern_for(rel)
            self.assertNotIn(",", p)                         # survives the comma-separated box
            self.assertEqual(split_patterns(", ".join([p, "*.log"])), [p, "*.log"])
            self.assertTrue(skipped([p], rel), rel)
        self.assertFalse(skipped([pattern_for("Photos [2024]")], "Photos 2"))
        self.assertFalse(skipped([pattern_for("a*b?/c.txt")], "aXXbY/c.txt"))
        self.assertFalse(skipped([pattern_for("Project/.venv")], "Other/.venv"))


class DescribeTests(unittest.TestCase):
    def test_picked_paths_read_as_names(self):
        from skip_picker import describe, is_rule
        self.assertEqual(describe(pattern_for("Project/Photos [2024]")), ("Photos [2024]", "Project/Photos [2024]"))
        self.assertEqual(describe(pattern_for("a*b/c?.txt")), ("c?.txt", "a*b/c?.txt"))
        self.assertEqual(describe("node_modules"), ("node_modules", "node_modules"))
        self.assertEqual(describe("*.log"), ("*.log", "*.log"))
        self.assertTrue(is_rule("*.log"))
        self.assertTrue(is_rule("Thumbs[0-9].db"))
        self.assertFalse(is_rule(pattern_for("Photos [2024]")))
        self.assertFalse(is_rule("Project/.venv"))


class PickerTests(unittest.TestCase):
    """Drive the real dialog (no screen interaction: actions are scheduled on its event loop)."""

    _root = None

    @classmethod
    def shared_root(cls):
        # One Tk for all these tests: starting Tk over and over in one process sometimes fails on
        # Windows ("Can't find a usable tk.tcl").
        import customtkinter as ctk
        if PickerTests._root is None:
            PickerTests._root = ctk.CTk()
            PickerTests._root.withdraw()
        return PickerTests._root

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for rel in ["Project/.venv/Lib/x.py", "Project/src/main.py", "Photos [2024]/a.jpg",
                    "Project/node_modules/m.js", "Thumbs.db"]:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
        self.root = self.shared_root()

    def open(self, patterns, steps):
        from skip_picker import SkipPicker
        seen = {}

        def run():
            picker = self.root.winfo_children()[-1]
            steps(picker, seen)
        self.root.after(300, run)
        result = SkipPicker(self.root, self.tmp.name, patterns, always=["Thumbs.db"]).result
        return result, seen

    def test_reopens_with_saved_choices_and_toggles(self):
        def steps(p, seen):
            seen["common"] = p.common["node_modules"].get()
            seen["other"] = list(p.other)
            venv = p.item_of["Project/.venv"]
            seen["revealed"] = p.tree.item(p.item_of["Project"], "open")
            seen["venv_text"] = p.tree.item(venv, "text")
            p.toggle(p.item_of["Photos [2024]"])                      # tick
            p.toggle(p.item_of["Project/.venv"])                      # untick
            p.toggle(p.item_of["Project"])                            # tick the parent...
            seen["src_text"] = p.tree.item(p.item_of["Project/src"], "text")
            p.toggle(p.item_of["Project/src"])                        # ...so its children can't change
            seen["nm_text"] = p.tree.item(p.item_of["Project/node_modules"], "text")
            seen["thumbs_text"] = p.tree.item(p.item_of["Thumbs.db"], "text")
            p.done()

        result, seen = self.open(["node_modules", "Project/.venv", "*.psd"], steps)
        self.assertTrue(seen["common"])
        self.assertEqual(seen["other"], ["*.psd"])
        self.assertTrue(seen["revealed"])
        self.assertTrue(seen["venv_text"].startswith("☑"))
        self.assertTrue(seen["src_text"].startswith("▣"))            # inside a ticked folder
        self.assertTrue(seen["nm_text"].startswith("▣"))             # skipped by the node_modules rule
        self.assertTrue(seen["thumbs_text"].startswith("▣"))         # built-in junk
        self.assertEqual(result, ["node_modules", "Photos [[]2024]", "Project", "*.psd"])
        self.assertTrue(skipped(result, "Photos [2024]/a.jpg".split("/")[0]))

    def test_cancel_returns_none(self):
        result, _ = self.open(["*.log"], lambda p, seen: p.destroy())
        self.assertIsNone(result)

    def test_missing_folder_still_opens(self):
        from skip_picker import SkipPicker

        def steps():
            picker = self.root.winfo_children()[-1]
            picker.done()
        self.root.after(300, steps)
        self.assertEqual(SkipPicker(self.root, str(Path(self.tmp.name) / "nope"), ["Project/.venv"]).result,
                         ["Project/.venv"])


class PathDrive(TwoWayFakeDrive):
    def find_folder(self, name, parent):
        return next((i["id"] for i in self.list_children(parent)
                     if i["name"] == name and i["mimeType"] == FOLDER_MIME), None)

    def mkdirs(self, path):
        cur = "root"
        for part in path.split("/"):
            cur = self.find_folder(part, cur) or self.create_folder(part, cur)
        return cur


class SkipOnDriveSyncTests(unittest.TestCase):
    """A skipped item that's only on Drive is never downloaded, and nothing is deleted on either side."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pc = Path(self.tmp.name) / "pc"
        self.pc.mkdir()
        self.state = Path(self.tmp.name) / "s.json"
        self.drive = PathDrive()
        self.drive.mkdirs("Big Drive Only [2024]/Raw")
        self.drive.web_add("Big Drive Only [2024]/Raw", "clip.mp4", "video")
        self.drive.web_add("", "keep.txt", "k")
        (self.pc / "a.txt").write_text("a")

    def sync(self, skip):
        return TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES + skip,
                            state_path=self.state, recycle=lambda p: self.fail(f"recycled {p}")).run()

    def test_skipped_drive_folder_is_not_downloaded(self):
        self.sync([pattern_for("Big Drive Only [2024]")])
        self.assertEqual(local_tree(self.pc), {"a.txt": "a", "keep.txt": "k"})
        self.assertIn("Big Drive Only [2024]/Raw/clip.mp4", self.drive.tree())
        self.assertIn("a.txt", self.drive.tree())

    def test_skipping_a_synced_folder_later_deletes_nothing(self):
        self.sync([])
        self.assertTrue((self.pc / "Big Drive Only [2024]/Raw/clip.mp4").exists())
        s = self.sync([pattern_for("Big Drive Only [2024]")])
        self.assertEqual((s["trashed"], s["deleted_local"]), (0, 0))
        self.assertTrue((self.pc / "Big Drive Only [2024]/Raw/clip.mp4").exists())
        self.assertIn("Big Drive Only [2024]/Raw/clip.mp4", self.drive.tree())


    def quick_sync(self, skip, paths=()):
        """What the app does after a change: only look at the folders that changed."""
        syncer = TwoWaySyncer(self.drive, self.pc, "root", excludes=DEFAULT_EXCLUDES + skip,
                              state_path=self.state, recycle=lambda p: self.fail(f"recycled {p}"))
        targets = syncer.targets_for(paths, self.drive.take_changes())
        self.assertIsNotNone(targets)                                # really a quick sync
        return syncer.run(targets)

    def test_quick_sync_never_goes_into_a_folder_skipped_after_it_was_synced(self):
        self.sync([])                                                # synced before it was skipped
        skip = [pattern_for("Big Drive Only [2024]")]
        self.drive.take_changes()
        self.drive.web_add("Big Drive Only [2024]/Raw", "new.mp4", "new video")
        self.drive.web_edit("Big Drive Only [2024]/Raw/clip.mp4", "edited on Drive")
        s = self.quick_sync(skip)
        self.assertEqual(s["downloaded"], 0)
        self.assertFalse((self.pc / "Big Drive Only [2024]/Raw/new.mp4").exists())
        self.assertEqual((self.pc / "Big Drive Only [2024]/Raw/clip.mp4").read_text(), "video")

    def test_quick_sync_never_uploads_from_a_skipped_folder(self):
        self.sync([])
        skip = [pattern_for("Big Drive Only [2024]")]
        self.drive.take_changes()
        new = self.pc / "Big Drive Only [2024]/Raw/from pc.txt"
        new.write_text("pc")
        s = self.quick_sync(skip, paths=[str(new)])
        self.assertEqual(s["uploaded"], 0)
        self.assertNotIn("Big Drive Only [2024]/Raw/from pc.txt", self.drive.tree())

    def test_new_drive_file_in_a_skipped_drive_only_folder_quick_sync(self):
        skip = [pattern_for("Big Drive Only [2024]")]
        self.sync(skip)
        self.drive.take_changes()
        self.drive.web_add("Big Drive Only [2024]/Raw", "new.mp4", "new video")
        self.drive.web_add("", "top.txt", "t")
        self.quick_sync(skip)
        self.assertEqual(local_tree(self.pc), {"a.txt": "a", "keep.txt": "k", "top.txt": "t"})


class DrivePickerTests(PickerTests):
    """The picker shows what's only on Google Drive (fetched in the background) and can skip it."""

    def setUp(self):
        super().setUp()
        self.drive = PathDrive()
        self.drive.mkdirs("EBC/Project/Old on Drive")
        self.drive.mkdirs("EBC/Big Drive Only")
        self.drive.web_add("EBC/Big Drive Only", "clip.mp4", "video")
        self.drive.web_add("EBC", "report.pdf", "r")
        self.app = types.SimpleNamespace(drive_for_browsing=lambda email: self.drive)

    def open_drive(self, patterns, steps):
        from skip_picker import SkipPicker, drive_lister
        seen = {}

        def wait_then(p, fn, tries=100):
            if not p.loading_row or tries == 0:
                fn()
            else:
                p.after(50, lambda: wait_then(p, fn, tries - 1))

        def run():
            p = self.root.winfo_children()[-1]
            steps(p, seen, lambda fn: wait_then(p, fn))
        self.root.after(200, run)
        lister = drive_lister(self.app, "me@example.com", None, "My Drive/EBC")
        result = SkipPicker(self.root, self.tmp.name, patterns, drive_list=lister, drive_label="My Drive/EBC").result
        return result, seen

    def test_drive_only_items_show_and_can_be_skipped(self):
        def steps(p, seen, wait):
            def loaded():
                seen["top"] = [p.tree.item(c, "text") for c in p.tree.get_children("")]
                big = p.item_of["Big Drive Only"]
                p._fill(big, "Big Drive Only")                       # open a folder only on Drive
                p._fill(p.item_of["Project"], "Project")             # and one on both sides
                wait(after_open)

            def after_open():
                seen["big"] = [p.tree.item(c, "text") for c in p.tree.get_children(p.item_of["Big Drive Only"])]
                seen["project"] = [p.tree.item(c, "text") for c in p.tree.get_children(p.item_of["Project"])]
                p.toggle(p.item_of["Big Drive Only"])
                p.toggle(p.item_of["report.pdf"])
                p.done()
            wait(loaded)

        result, seen = self.open_drive([], steps)
        top = "\n".join(seen["top"])
        self.assertIn("Big Drive Only   ·  only on Google Drive", top)
        self.assertIn("report.pdf   ·  only on Google Drive", top)
        self.assertNotIn("Project   ·", top)                          # on both sides: no marker
        self.assertEqual(sum("Project" in t for t in seen["top"]), 1)  # listed once
        self.assertTrue(any("clip.mp4" in t for t in seen["big"]))
        self.assertTrue(any("Old on Drive" in t and "only on Google Drive" in t for t in seen["project"]))
        self.assertEqual(result, ["Big Drive Only", "report.pdf"])

    def test_reopening_ticks_saved_drive_only_items(self):
        def steps(p, seen, wait):
            seen["before"] = list(p.other)

            def loaded():
                seen["after"] = list(p.other)
                seen["text"] = p.tree.item(p.item_of["Big Drive Only"], "text")
                p.done()
            wait(loaded)

        result, seen = self.open_drive(["Big Drive Only", "*.psd"], steps)
        self.assertEqual(seen["before"], ["Big Drive Only", "*.psd"])
        self.assertEqual(seen["after"], ["*.psd"])
        self.assertTrue(seen["text"].startswith("☑"))
        self.assertEqual(result, ["Big Drive Only", "*.psd"])

    def test_drive_folder_not_created_yet(self):
        from skip_picker import drive_lister
        lister = drive_lister(self.app, "me@example.com", None, "My Drive/Not There")
        self.assertEqual(lister(None), [])
        self.assertNotIn("Not There", self.drive.tree())


if __name__ == "__main__":
    unittest.main()
