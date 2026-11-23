"""Tests for the Add / Edit folder rules in ui_model (shared by the Tk and Qt dialogs).
Run:  python -m unittest test_ui_model_folder_dialog -v
"""
import os
import tempfile
import unittest

import ui_model as m

A = "teacher@example.com"


class FolderResultTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name

    def result(self, data=None, **kw):
        args = dict(local=self.dir, account=A, mode="two_way", delete=False, exclude_text="*.log, node_modules",
                    exclude_where={}, drive_text="My Drive/PC Backup/Photos", drive_id=None, chosen_label=None)
        args.update(kw)
        return m.folder_result(data or {}, **args)

    def test_typed_drive_path(self):
        r = self.result()
        self.assertEqual(r, {"local": os.path.normpath(self.dir), "account": A, "mode": "two_way", "delete": False,
                             "exclude": ["*.log", "node_modules"], "drive_folder": "PC Backup/Photos"})

    def test_chosen_drive_folder_is_kept_by_id(self):
        r = self.result(drive_text="Shared drive › Staff", drive_id="abc", chosen_label="Shared drive › Staff")
        self.assertEqual((r["drive_folder_id"], r["drive_folder"]), ("abc", "Shared drive › Staff"))
        r = self.result(drive_text="My Drive/Other", drive_id="abc", chosen_label="Shared drive › Staff")
        self.assertNotIn("drive_folder_id", r)                       # typed over the choice: the typed path
        self.assertEqual(r["drive_folder"], "Other")

    def test_empty_drive_path_gets_a_default(self):
        r = self.result(drive_text="  ")
        self.assertEqual(r["drive_folder"], f"PC Backup/{os.path.basename(self.dir)}")

    def test_mirror_delete_only_for_upload(self):
        self.assertFalse(self.result(mode="two_way", delete=True)["delete"])
        self.assertTrue(self.result(mode="upload", delete=True)["delete"])

    def test_skip_sides_kept_only_for_listed_patterns(self):
        r = self.result(exclude_where={"*.log": "pc", "gone": "drive"})
        self.assertEqual(r["exclude_where"], {"*.log": "pc"})
        r = self.result(data={"exclude_where": {"old": "pc"}}, exclude_where={})
        self.assertNotIn("exclude_where", r)

    def test_no_account_and_editing_keeps_other_keys(self):
        r = self.result(data={"waiting": True, "account": "old@example.com", "drive_folder_id": "x"}, account=None)
        self.assertNotIn("account", r)
        self.assertNotIn("drive_folder_id", r)
        self.assertTrue(r["waiting"])

    def test_missing_pc_folder(self):
        self.assertIsNone(self.result(local=os.path.join(self.dir, "nope")))
        self.assertIsNone(self.result(local="  "))


class AddFolderTests(unittest.TestCase):
    def test_new_folders_wait_and_duplicates_are_refused(self):
        cfg = {"folders": [{"local": r"D:\EBC", "account": A}], "appearance": "Dark"}

        def account(f, known):
            return f.get("account")
        out = m.add_folder(cfg, {"local": r"D:\Photos", "account": A}, [A], account)
        self.assertEqual(out["folders"][-1], {"local": r"D:\Photos", "account": A, "waiting": True})
        self.assertEqual(out["appearance"], "Dark")
        self.assertIsNone(m.add_folder(cfg, {"local": r"d:\ebc", "account": A}, [A], account))   # same folder
        self.assertIsNotNone(m.add_folder(cfg, {"local": r"D:\EBC", "account": "b@x.com"}, [A], account))

    def test_replace_folder(self):
        cfg = {"folders": [{"local": "A"}, {"local": "B"}]}
        self.assertEqual(m.replace_folder(cfg, 1, {"local": "C"})["folders"], [{"local": "A"}, {"local": "C"}])
        self.assertEqual(cfg["folders"][1], {"local": "B"})


class DialogTextTests(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(list(m.MODES.values()), ["two_way", "upload"])
        self.assertIn("Changes on either side", m.mode_help("two_way"))
        self.assertIn("master copy", m.mode_help("upload"))
        self.assertEqual(m.drive_text_for({"drive_folder": "PC Backup/X"}), "My Drive/PC Backup/X")
        self.assertEqual(m.drive_text_for({"drive_folder": "Staff", "drive_folder_id": "id"}), "Staff")
        self.assertEqual(m.default_drive_text(r"D:\Photos"), "My Drive/PC Backup/Photos")


if __name__ == "__main__":
    unittest.main()
