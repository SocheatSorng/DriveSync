"""Tests for multi-account storage and folder -> account mapping (no network needed).
Run:  python -m unittest test_accounts -v
"""
import json
import tempfile
import unittest
from pathlib import Path

import accounts


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "accounts"

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_when_no_folder(self):
        self.assertEqual(accounts.list_accounts(self.base), [])

    def test_save_list_remove(self):
        accounts.save_account("Work@Example.com", json.dumps({"token": "x"}), self.base)
        accounts.save_account("me@gmail.com", json.dumps({"token": "y"}), self.base)
        self.assertEqual(accounts.list_accounts(self.base), ["me@gmail.com", "work@example.com"])
        accounts.remove_account("work@example.com", self.base, revoke=False)
        self.assertEqual(accounts.list_accounts(self.base), ["me@gmail.com"])
        accounts.remove_account("not-there@gmail.com", self.base, revoke=False)  # no error

    def test_rejects_names_that_could_escape_the_folder(self):
        for bad in ["..\\evil@x.com", "a/b@x.com", "nope", "x@y", "a@b.com:stream", ""]:
            with self.assertRaises(ValueError, msg=bad):
                accounts.token_path(bad, self.base)

    def test_unknown_account_needs_sign_in(self):
        with self.assertRaises(accounts.NeedsSignIn):
            accounts.load_credentials("someone@gmail.com", self.base)

    def test_stray_files_are_not_accounts(self):
        self.base.mkdir(parents=True)
        (self.base / "notes.json").write_text("{}")
        (self.base / "a@b.com.tmp").write_text("{}")
        self.assertEqual(accounts.list_accounts(self.base), [])


class FolderAccountTests(unittest.TestCase):
    def test_folder_account(self):
        from tray_app import App
        known = ["a@gmail.com", "b@gmail.com"]
        self.assertEqual(App.folder_account({"account": "B@gmail.com"}, known), "b@gmail.com")
        self.assertEqual(App.folder_account({}, known), "a@gmail.com")          # older config: first account
        self.assertIsNone(App.folder_account({}, []))
        self.assertEqual(App.folder_account({"account": "gone@gmail.com"}, known), "gone@gmail.com")


if __name__ == "__main__":
    unittest.main()
