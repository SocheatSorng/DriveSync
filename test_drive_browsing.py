"""Tests for full-Drive-access features: old-login detection, folder browsing, paths.
Run:  python -m unittest test_drive_browsing -v
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import accounts
from drive_sync import FOLDER_MIME, Drive, resolve_remote_root


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def token(self, scopes):
        return accounts.save_account("me@gmail.com", json.dumps({"token": "t", "scopes": scopes}), self.base)

    def test_old_narrow_login_must_sign_in_again(self):
        p = self.token(["https://www.googleapis.com/auth/drive.file"])
        self.assertFalse(accounts.has_required_scopes(p))
        with self.assertRaises(accounts.NeedsSignIn):
            accounts.load_credentials("me@gmail.com", self.base)

    def test_full_access_login_is_fine(self):
        self.assertTrue(accounts.has_required_scopes(self.token(["https://www.googleapis.com/auth/drive"])))
        self.assertTrue(accounts.has_required_scopes(self.token("https://www.googleapis.com/auth/drive")))

    def test_unreadable_token_counts_as_needing_sign_in(self):
        p = self.base / "x@y.com.json"
        p.write_text("not json")
        self.assertFalse(accounts.has_required_scopes(p))


def fake_service(pages):
    """A Drive service whose files().list() returns the given pages in order."""
    svc = mock.MagicMock()
    svc.files.return_value.list.return_value.execute.side_effect = pages
    return svc


class DriveBrowsingTests(unittest.TestCase):
    def test_list_subfolders_pages_sorts_and_includes_shared_drives(self):
        svc = fake_service([
            {"files": [{"id": "2", "name": "beta"}], "nextPageToken": "p2"},
            {"files": [{"id": "1", "name": "Alpha"}]},
        ])
        out = Drive(svc).list_subfolders("root")
        self.assertEqual([f["name"] for f in out], ["Alpha", "beta"])
        kwargs = svc.files.return_value.list.call_args_list[0].kwargs
        self.assertIn(f"mimeType = '{FOLDER_MIME}'", kwargs["q"])
        self.assertTrue(kwargs["supportsAllDrives"])
        self.assertTrue(kwargs["includeItemsFromAllDrives"])

    def test_uploads_work_in_shared_drives(self):
        svc = mock.MagicMock()
        svc.files.return_value.create.return_value.execute.return_value = {"id": "new"}
        Drive(svc).create_folder("X", "shared-drive-folder")
        self.assertTrue(svc.files.return_value.create.call_args.kwargs["supportsAllDrives"])

    def test_query_values_are_escaped(self):
        svc = fake_service([{"files": []}])
        Drive(svc).find_folder("Bob's \\ files", "root")
        q = svc.files.return_value.list.call_args.kwargs["q"]
        self.assertIn("name = 'Bob\\'s \\\\ files'", q)


class PathTests(unittest.TestCase):
    def test_my_drive_prefix_is_not_created_as_a_folder(self):
        drive = mock.MagicMock()
        drive.find_folder.return_value = None
        drive.create_folder.side_effect = lambda name, parent: f"{parent}/{name}"
        self.assertEqual(resolve_remote_root(drive, "My Drive/PC Backup/Photos", create=True),
                         "root/PC Backup/Photos")
        created = [c.args[0] for c in drive.create_folder.call_args_list]
        self.assertEqual(created, ["PC Backup", "Photos"])

    def test_gui_path_helper(self):
        import gui
        self.assertEqual(gui._strip_my_drive("My Drive/School/2026"), "School/2026")
        self.assertEqual(gui._strip_my_drive("my drive"), "")
        self.assertEqual(gui._strip_my_drive("PC Backup\\Photos/"), "PC Backup/Photos")


if __name__ == "__main__":
    unittest.main()
