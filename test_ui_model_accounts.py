"""Tests for the Google accounts texts and rules in ui_model (shared by the Tk and Qt windows).
Run:  python -m unittest test_ui_model_accounts -v
"""
import types
import unittest

import ui_model as m

EMAIL = "teacher@example.com"


def profile(pid, label, directory):
    return types.SimpleNamespace(id=pid, label=label, directory=directory)


class ProfileChoiceTests(unittest.TestCase):
    def test_choices_start_with_the_default_browser_and_stay_unique(self):
        choices = m.profile_choices([profile("chrome|Default", "Chrome - Work", "Default"),
                                     profile("chrome|Profile 1", "Chrome - Work", "Profile 1"),
                                     profile("edge|Default", "Edge - Me", "Default")])
        self.assertEqual(list(choices), ["Default browser", "Chrome - Work", "Chrome - Work (Profile 1)",
                                         "Edge - Me"])
        self.assertIsNone(choices["Default browser"])
        self.assertEqual(choices["Chrome - Work (Profile 1)"], "chrome|Profile 1")

    def test_label_for(self):
        choices = {"Default browser": None, "Edge - Me": "edge|Default"}
        self.assertEqual(m.profile_label(None, choices), "Default browser")
        self.assertEqual(m.profile_label("edge|Default", choices), "Edge - Me")
        self.assertEqual(m.profile_label("brave|Profile 9", choices), "Missing profile (Profile 9)")


class AccountActionTests(unittest.TestCase):
    def test_remove_question(self):
        q = m.remove_account_question(EMAIL, 0)
        self.assertTrue(q.startswith(f"Remove {EMAIL} from Drive Sync?"))
        self.assertNotIn("stop syncing", q)
        self.assertIn("2 folders use this account and will stop syncing", m.remove_account_question(EMAIL, 2))
        self.assertIn("1 folder use this account", m.remove_account_question(EMAIL, 1))

    def test_new_account_goes_to_folders_without_one(self):
        cfg = {"folders": [{"local": "A"}, {"local": "B", "account": "x@example.com"}], "appearance": "Dark"}
        out = m.assign_account(cfg, EMAIL)
        self.assertEqual([f.get("account") for f in out["folders"]], [EMAIL, "x@example.com"])
        self.assertEqual(out["appearance"], "Dark")
        self.assertNotIn("account", cfg["folders"][0])                  # the original is left as it was
        self.assertIsNone(m.assign_account({"folders": [{"local": "B", "account": "x"}]}, EMAIL))   # no change
        self.assertIsNone(m.assign_account({}, EMAIL))

    def test_texts(self):
        self.assertIn("Sign in with a Google account now?", m.IMPORTED_ASK)
        self.assertEqual(m.ADD_ACCOUNT_TITLE, "Add Google account")
        self.assertIn("browser profile", m.ADD_ACCOUNT_TEXT)


if __name__ == "__main__":
    unittest.main()
