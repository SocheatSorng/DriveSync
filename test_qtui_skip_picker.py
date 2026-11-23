"""Tests for skip_model.SkipModel and the Qt skip picker (qtui/skip_picker.py), offscreen - the same
situations the Tk picker's tests (test_skip_picker.py) check. Drive is a fake lister; nothing syncs.
Run:  python -m unittest test_qtui_skip_picker -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from skip_model import INHERITED, OFF, ON, SkipModel, pattern_for

try:
    from PySide6.QtWidgets import QApplication, QCheckBox
except ImportError:  # pragma: no cover
    QApplication = None

if QApplication is not None:
    from qtui.skip_picker import SkipPickerDialog, pick_skips
    from test_qtui import dispose

FILES = ["Project/.venv/Lib/x.py", "Project/src/main.py", "Photos [2024]/a.jpg", "Project/node_modules/m.js",
         "Thumbs.db"]


def make_tree(root: Path) -> None:
    for rel in FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")


def fake_drive_list():
    """Drive side: Project (also on the PC) with an extra file, and two things only on Drive."""
    listing = {None: [{"name": "Project", "id": "p", "is_dir": True},
                      {"name": "Big Drive Only", "id": "b", "is_dir": True},
                      {"name": "report.pdf", "id": "r", "is_dir": False}],
               "p": [{"name": "Old on Drive", "id": "o", "is_dir": True}, {"name": "src", "id": "s", "is_dir": True}],
               "b": [{"name": "clip.mp4", "id": "c", "is_dir": False}], "o": [], "s": []}
    return lambda folder_id: listing[folder_id]


class SkipModelTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        make_tree(self.root)

    def test_saved_patterns(self):
        m = SkipModel(str(self.root), ["node_modules", "Project/.venv", "*.psd"], always=["Thumbs.db"])
        self.assertTrue(m.common["node_modules"])
        self.assertEqual(m.paths, {"Project/.venv"})
        self.assertEqual(m.other, ["*.psd"])
        self.assertEqual(m.ticked_rels, ["Project/.venv"])

    def test_inherited_and_toggling(self):
        m = SkipModel(str(self.root), ["node_modules"], always=["Thumbs.db"])
        self.assertTrue(m.inherited("Project/node_modules"))       # by the node_modules rule
        self.assertTrue(m.inherited("Thumbs.db"))                  # built-in junk
        self.assertTrue(m.toggle("Project"))
        self.assertTrue(m.inherited("Project/src"))                # inside a ticked folder
        self.assertFalse(m.toggle("Project/src"))
        self.assertTrue(m.toggle("Photos [2024]"))
        self.assertEqual(m.result_patterns(), ["node_modules", "Photos [[]2024]", "Project"])
        self.assertEqual(m.row_text("Project/src", True, False)[0], f"{INHERITED}  📁  src")
        self.assertEqual(m.row_text("Project", True, False)[0], f"{ON}  📁  Project")
        self.assertEqual(m.row_text("x.txt", False, True), (f"{OFF}  📄  x.txt   ·  only on Google Drive", "drive"))

    def test_listing_and_drive_merge(self):
        m = SkipModel(str(self.root), ["Big Drive Only"], has_drive=True)
        entries, more, error = m.list_pc("")
        self.assertEqual([n for n, _d in entries], ["Photos [2024]", "Project", "Thumbs.db"])
        self.assertEqual((more, error), (0, None))
        self.assertEqual(m.drive_to_ask(""), (True, None))
        self.assertEqual(m.drive_to_ask(""), (False, None))                # only once
        here = {n.lower(): n for n, _d in entries}
        added = m.merge_drive("", here, fake_drive_list()(None))
        self.assertEqual(added, [("Big Drive Only", True), ("report.pdf", False)])
        m.adopt([n for n, _d in entries] + [r for r, _d in added])
        self.assertIn("Big Drive Only", m.paths)                           # saved, only on Drive: now ticked
        self.assertEqual(m.where_of("Project"), "both")
        self.assertEqual(m.where_of("Big Drive Only"), "drive")
        self.assertEqual(m.where_of("Photos [2024]"), "pc")
        self.assertEqual(m.drive_to_ask("Project"), (True, "p"))

    def test_finish_records_sides_and_keeps_old_ones(self):
        m = SkipModel(str(self.root), ["Gone"], where={"Gone": "drive"}, has_drive=True)
        m.list_pc("")
        m.merge_drive("", {}, [{"name": "Project", "id": "p", "is_dir": True}])
        m.toggle("Project")
        m.toggle("Photos [2024]")
        patterns, where = m.finish(["Project", "Photos [2024]"])
        self.assertEqual(where, {"Project": "both", pattern_for("Photos [2024]"): "pc", "Gone": "drive"})
        self.assertIn("Gone", patterns)

    def test_missing_folder(self):
        m = SkipModel(str(self.root / "nope"), ["Project/.venv"])
        self.assertFalse(m.can_show())
        self.assertEqual(m.result_patterns(), ["Project/.venv"])


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class QtSkipPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        make_tree(self.root)

    def picker(self, patterns, drive_list=None, where=None):
        p = SkipPickerDialog(None, str(self.root), patterns, always=["Thumbs.db"], drive_list=drive_list,
                             drive_label="My Drive/EBC" if drive_list else "", where=where, run_now=True)
        self.addCleanup(dispose, p)
        return p

    def text(self, p, rel):
        return p.item_of[rel].text(0)

    def test_reopens_with_saved_choices_and_toggles(self):
        p = self.picker(["node_modules", "Project/.venv", "*.psd"])
        common = {c.text().split("  ·  ")[0]: c for c in p.findChildren(QCheckBox)}
        self.assertTrue(common["node_modules"].isChecked())
        self.assertTrue(p.item_of["Project"].isExpanded())                 # opened to show the saved tick
        self.assertTrue(self.text(p, "Project/.venv").startswith(ON))
        self.assertEqual(p.other_lbl.text(), "Also skipping (typed): *.psd")
        p.toggle("Photos [2024]")
        p.toggle("Project/.venv")
        p.toggle("Project")
        self.assertTrue(self.text(p, "Project/src").startswith(INHERITED))
        p.toggle("Project/src")                                            # can't change: inside a ticked folder
        self.assertTrue(self.text(p, "Project/node_modules").startswith(INHERITED))
        self.assertTrue(self.text(p, "Thumbs.db").startswith(INHERITED))
        p.done_btn.click()
        self.assertEqual(p.result, ["node_modules", "Photos [[]2024]", "Project", "*.psd"])

    def test_common_tick_boxes(self):
        p = self.picker([])
        common = {c.text().split("  ·  ")[0]: c for c in p.findChildren(QCheckBox)}
        common["*.log"].click()
        self.assertEqual(p.count_lbl.text(), "1 rule")
        p.done_btn.click()
        self.assertEqual(p.result, ["*.log"])

    def test_cancel_returns_none(self):
        p = self.picker(["*.log"])
        p.reject()
        self.assertIsNone(p.result)

    def test_missing_folder_still_opens(self):
        p = SkipPickerDialog(None, str(self.root / "nope"), ["Project/.venv"], run_now=True)
        self.addCleanup(dispose, p)
        p.done_btn.click()
        self.assertEqual(p.result, ["Project/.venv"])

    def test_drive_only_items_show_and_can_be_skipped(self):
        p = self.picker([], drive_list=fake_drive_list())
        top = [p.tree.topLevelItem(i).text(0) for i in range(p.tree.topLevelItemCount())]
        self.assertIn(f"{OFF}  📁  Big Drive Only   ·  only on Google Drive", top)
        self.assertIn(f"{OFF}  📄  report.pdf   ·  only on Google Drive", top)
        self.assertEqual(sum("Project" in t for t in top), 1)              # on both sides: listed once
        p.expand("Big Drive Only")
        self.assertIn("clip.mp4", self.text(p, "Big Drive Only/clip.mp4"))
        p.expand("Project")
        self.assertIn("only on Google Drive", self.text(p, "Project/Old on Drive"))
        p.toggle("Big Drive Only")
        p.toggle("report.pdf")
        p.toggle("Project")
        p.toggle("Photos [2024]")
        p.done_btn.click()
        self.assertEqual(p.result, ["Big Drive Only", "Photos [[]2024]", "Project", "report.pdf"])
        self.assertEqual(p.result_where, {"Big Drive Only": "drive", "report.pdf": "drive", "Project": "both",
                                          pattern_for("Photos [2024]"): "pc"})

    def test_reopening_ticks_saved_drive_only_items(self):
        p = self.picker(["Big Drive Only", "*.psd"], drive_list=fake_drive_list())
        self.assertTrue(self.text(p, "Big Drive Only").startswith(ON))
        p.done_btn.click()
        self.assertEqual(p.result, ["Big Drive Only", "*.psd"])

    def test_drive_error_shows_in_the_tree(self):
        def broken(_folder_id):
            raise OSError("No internet")
        p = self.picker([], drive_list=broken)
        top = [p.tree.topLevelItem(i).text(0) for i in range(p.tree.topLevelItemCount())]
        self.assertIn("Couldn't read Google Drive: No internet", top)

    def test_pick_skips_from_the_folder_dialog(self):
        with mock.patch.object(SkipPickerDialog, "exec", lambda self: self.done_btn.click()):
            out = pick_skips(None, mock.MagicMock(), str(self.root), "*.log", None, "", None, None, {})
        self.assertEqual(out, (["*.log"], {}))


if __name__ == "__main__":
    unittest.main()
