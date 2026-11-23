"""Tests for the window's texts and decisions (ui_model.py), shared by the Tk and Qt windows.
Run:  python -m unittest test_ui_model -v
"""
import os
import tempfile
import types
import unittest

import ui_model as m

EMAIL = "teacher@example.com"


def folder(**kw):
    return {"local": r"D:\Storage\Image", "account": EMAIL, "mode": "two_way",
            "drive_folder": "PC Backup/Image", **kw}


class FolderCardTests(unittest.TestCase):
    def card(self, f=None, email=EMAIL, known=(EMAIL,), signin=(), waiting=False, exists=True):
        return m.folder_card(f or folder(), email, list(known), set(signin), waiting, exists)

    def test_title_and_location(self):
        c = self.card()
        self.assertEqual((c.title, c.location), ("Image", r"D:\Storage"))
        c = self.card(folder(local="D:\\"))                      # a whole drive: shown whole, no location
        self.assertEqual((c.title, c.location), ("D:\\", ""))

    def test_line_for_a_two_way_folder(self):
        c = self.card()
        self.assertEqual(c.line, f"↔  {EMAIL} · My Drive/PC Backup/Image · two-way")
        self.assertEqual(c.tone, "muted")
        self.assertFalse(c.waiting)

    def test_upload_only_and_drive_browser_target(self):
        c = self.card(folder(mode="upload", delete=True, drive_folder_id="abc",
                             drive_folder="Shared drive › Staff › Plans"))
        self.assertEqual(c.line, f"→  {EMAIL} · Shared drive › Staff › Plans · upload only, Drive matches PC")
        c = self.card(folder(drive_folder_id="abc", drive_folder=""))
        self.assertIn("folder id abc", c.line)
        c = self.card(folder(drive_folder="My Drive/School"))     # typed with "My Drive/": not doubled
        self.assertIn("· My Drive/School ·", c.line)

    def test_problems_in_order_of_importance(self):
        self.assertEqual(self.card(exists=False).line, "⚠  This folder no longer exists on this PC")
        self.assertEqual(self.card(email=None).line, "⚠  No Google account - add one in Settings")
        self.assertEqual(self.card(known=()).line, f"⚠  Account {EMAIL} was removed - click Edit to choose another")
        self.assertEqual(self.card(signin=(EMAIL,)).line,
                         f"⚠  {EMAIL} needs to sign in again (Settings → Google accounts)")
        for c in (self.card(exists=False), self.card(email=None), self.card(known=()), self.card(signin=(EMAIL,))):
            self.assertEqual(c.tone, "error")

    def test_waiting_folder(self):
        c = self.card(waiting=True)
        self.assertEqual(c.line, f"Waiting - press Start syncing   ·   ↔  {EMAIL} · My Drive/PC Backup/Image · two-way")
        self.assertEqual(c.tone, "waiting")
        self.assertTrue(c.waiting and c.can_start)
        self.assertFalse(self.card(waiting=True, signin=(EMAIL,)).can_start)   # can't start until signed in
        self.assertFalse(self.card(waiting=True, exists=False).can_start)


class FolderActionTests(unittest.TestCase):
    def test_drive_url(self):
        known = [EMAIL]
        f = folder(drive_folder_id="abc123")
        self.assertEqual(m.drive_url(f, EMAIL, known, {}),
                         "https://drive.google.com/drive/folders/abc123?authuser=teacher%40example.com")
        f = folder()                                   # typed path: the id found by the last sync
        ids = {(os.path.normcase(f["local"]), EMAIL): "found1"}
        self.assertEqual(m.drive_url(f, EMAIL, known, ids),
                         "https://drive.google.com/drive/folders/found1?authuser=teacher%40example.com")
        self.assertEqual(m.drive_url(f, EMAIL, known, {}),
                         "https://drive.google.com/drive/my-drive?authuser=teacher%40example.com")

    def test_drive_url_without_a_folder(self):
        self.assertEqual(m.drive_url(None, None, [EMAIL], {}),
                         "https://drive.google.com/drive/my-drive?authuser=teacher%40example.com")
        self.assertEqual(m.drive_url(None, None, [EMAIL, "b@example.com"], {}),
                         "https://drive.google.com/drive/my-drive")    # several accounts: no guessing
        self.assertEqual(m.drive_url(None, None, [], {}), "https://drive.google.com/drive/my-drive")

    def test_drive_url_email(self):
        self.assertEqual(m.drive_url_email(None, [EMAIL]), EMAIL)
        self.assertIsNone(m.drive_url_email(None, [EMAIL, "b@example.com"]))
        self.assertEqual(m.drive_url_email(EMAIL, []), EMAIL)

    def test_without_folder(self):
        cfg = {"folders": [folder(local="A"), folder(local="B"), folder(local="C")], "interval_minutes": 15}
        out = m.without_folder(cfg, 1)
        self.assertEqual([f["local"] for f in out["folders"]], ["A", "C"])
        self.assertEqual(out["interval_minutes"], 15)
        self.assertEqual(len(cfg["folders"]), 3)       # the original is left as it was

    def test_remove_question(self):
        q = m.remove_folder_question(folder())
        self.assertIn(r"D:\Storage\Image", q)
        self.assertIn("Files already on Google Drive are not deleted.", q)

    def test_empty_texts(self):
        self.assertEqual(m.EMPTY_FOLDERS_TITLE, "Back up your first folder")
        self.assertIn("keep a copy in Google Drive", m.EMPTY_FOLDERS_TEXT)


class StripMyDriveTests(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(m.strip_my_drive("My Drive/School/2026"), "School/2026")
        self.assertEqual(m.strip_my_drive("my drive"), "")
        self.assertEqual(m.strip_my_drive("PC Backup\\Photos/"), "PC Backup/Photos")


class SkipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = tmp.name
        os.makedirs(os.path.join(self.local, "Old drafts"))

    def test_side(self):
        self.assertEqual(m.skip_side("*.log", self.local, {}), "rule")
        self.assertEqual(m.skip_side("node_modules", self.local, {}), "rule")       # a common name: anywhere
        self.assertEqual(m.skip_side("Old drafts", self.local, {}), "pc")
        self.assertEqual(m.skip_side("Raw exports", self.local, {}), "drive")      # not on this PC
        self.assertEqual(m.skip_side("Old drafts", self.local, {"Old drafts": "both"}), "both")

    def test_chips_short_names_sides_and_tooltips(self):
        chips = m.skip_chips(["*.log", "Raw exports"], self.local, {})
        self.assertEqual([c.text for c in chips.shown], ["*.log  ·  Rule", "Raw exports  ·  Google Drive"])
        self.assertEqual([c.tone for c in chips.shown], ["muted", "link"])
        self.assertIn("isn't downloaded to this PC", chips.shown[1].tooltip)
        self.assertTrue(chips.shown[1].tooltip.endswith("Click to change what's skipped."))
        self.assertIsNone(chips.more)

    def test_long_names_are_shortened_and_extra_chips_folded(self):
        long = "Photos/A very long folder name indeed for a chip"
        chips = m.skip_chips([long] + [f"*.x{i}" for i in range(8)], self.local, {})
        self.assertTrue(chips.shown[0].text.startswith("A very long folder name ind…"))
        self.assertIsNotNone(chips.more)
        self.assertEqual(chips.more.text, f"+{9 - len(chips.shown)} more")
        self.assertIn("(Rule)", chips.more.tooltip)
        self.assertEqual(chips.more.tone, "link")


class AccountTests(unittest.TestCase):
    def test_row(self):
        r = m.account_row({"email": EMAIL, "needs_signin": False, "folders": 2, "storage": "6.1 GB of 15 GB"})
        self.assertEqual((r.status, r.tone, r.dot), ("2 folders  ·  Drive: 6.1 GB of 15 GB", "muted", "ok"))
        r = m.account_row({"email": EMAIL, "needs_signin": False, "folders": 1, "storage": "14 GB of 15 GB",
                           "nearly_full": True})
        self.assertEqual((r.status, r.tone), ("1 folder  ·  Drive: 14 GB of 15 GB  -  nearly full", "error"))
        r = m.account_row({"email": EMAIL, "needs_signin": False, "folders": 0})
        self.assertEqual(r.status, "No folders yet")
        r = m.account_row({"email": EMAIL, "needs_signin": True, "folders": 3, "storage": "1 GB"})
        self.assertEqual((r.status, r.tone, r.dot), ("Click “Sign in again” to keep syncing (needs updated access)",
                                                     "error", "error"))
        self.assertTrue(r.needs_signin)

    def test_help_text_and_add_button(self):
        self.assertIn("import the OAuth client file", m.accounts_help(ready=False, busy=None, has_accounts=False))
        self.assertIn("Closed the browser? Press Cancel.", m.accounts_help(ready=True, busy="new", has_accounts=True))
        self.assertIn("Add the Google account", m.accounts_help(ready=True, busy=None, has_accounts=False))
        self.assertIn("Each folder syncs", m.accounts_help(ready=True, busy=None, has_accounts=True))
        self.assertEqual(m.add_account_button(ready=True, busy=None), (True, "+  Add Google account"))
        self.assertEqual(m.add_account_button(ready=True, busy="new"), (False, "Waiting for browser…"))
        self.assertEqual(m.add_account_button(ready=False, busy=None), (False, "+  Add Google account"))


class HeaderTests(unittest.TestCase):
    def app(self, **kw):
        base = dict(state="idle", paused=False, status="Up to date", detail="Last sync 17:51",
                    file_lines=["IMG_1.jpg"], progress=None)
        return types.SimpleNamespace(**{**base, **kw})

    def test_idle(self):
        h = m.header(self.app())
        self.assertEqual((h.dot, h.status, h.detail, h.pause_text, h.sync_enabled, h.progress),
                         ("idle", "Up to date", "Last sync 17:51", "Pause", True, None))

    def test_syncing_shows_one_line_per_file_and_progress(self):
        prog = types.SimpleNamespace(total=10, percent=40.0)
        h = m.header(self.app(state="syncing", detail="Uploading 3 of 10", file_lines=["a.jpg", "b.jpg"],
                              progress=prog))
        self.assertEqual(h.detail, "Uploading 3 of 10\na.jpg\nb.jpg")
        self.assertFalse(h.sync_enabled)
        self.assertAlmostEqual(h.progress, 0.4)
        no_total = types.SimpleNamespace(total=0, percent=0)
        self.assertIsNone(m.header(self.app(state="syncing", progress=no_total)).progress)

    def test_paused(self):
        h = m.header(self.app(paused=True))
        self.assertEqual((h.dot, h.pause_text), ("paused", "Resume"))


class UpdateLineTests(unittest.TestCase):
    def test_lines_and_button(self):
        upd = types.SimpleNamespace(version="1.2.0")
        self.assertEqual(m.update_line("1.1.0", True, "", None), ("Version 1.1.0 (installed)", None))
        self.assertEqual(m.update_line("1.1.0", True, "Checked 17:00", upd),
                         ("Version 1.1.0 (installed)\nChecked 17:00", "Update to 1.2.0"))
        self.assertEqual(m.update_line("1.1.0", False, "", upd),
                         ("Version 1.1.0 (not installed with Setup: updates are only shown)", "Download page"))


if __name__ == "__main__":
    unittest.main()
