"""Tests for multi-account storage and folder -> account mapping (no network needed).
Run:  python -m unittest test_accounts -v
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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


class CancelSignInTests(unittest.TestCase):
    """Closing the browser can't be seen, so "Cancel" must end the wait for Google's reply."""

    def test_cancel_ends_a_waiting_sign_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / "credentials.json"
            creds.write_text(json.dumps({"installed": {
                "client_id": "x.apps.googleusercontent.com", "client_secret": "test",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
            port = accounts.free_port()
            result = {}

            def run():
                try:
                    accounts.sign_in(creds, port=port, timeout=30)
                except Exception as e:  # noqa: BLE001
                    result["error"] = e

            with mock.patch("webbrowser.get"), mock.patch("browsers.register_controller", return_value=None):
                t = threading.Thread(target=run, daemon=True)
                t.start()
                self.assertTrue(accounts.cancel_sign_in(port))
                t.join(10)
            self.assertFalse(t.is_alive(), "sign-in still waiting after Cancel")
            self.assertIn("error", result)          # ended, without a login

    def test_cancel_with_nothing_waiting_gives_up(self):
        self.assertFalse(accounts.cancel_sign_in(accounts.free_port(), wait=0.3))


if __name__ == "__main__":
    unittest.main()
