import tempfile
import types
import unittest
from pathlib import Path

from skip_picker import pattern_for, split_patterns
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


class PickerTests(unittest.TestCase):
    """Drive the real dialog (no screen interaction: actions are scheduled on its event loop)."""

    def setUp(self):
        import customtkinter as ctk
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for rel in ["Project/.venv/Lib/x.py", "Project/src/main.py", "Photos [2024]/a.jpg",
                    "Project/node_modules/m.js", "Thumbs.db"]:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

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


if __name__ == "__main__":
    unittest.main()
