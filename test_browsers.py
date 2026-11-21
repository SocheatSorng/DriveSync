"""Tests for browser-profile detection and launching (no browser is actually started).
Run:  python -m unittest test_browsers -v
"""
import json
import tempfile
import unittest
import webbrowser
from pathlib import Path
from unittest import mock

import browsers


def make_user_data(root: Path, profiles: dict[str, str], deleted: tuple = ()) -> Path:
    ud = root / "User Data"
    ud.mkdir(parents=True)
    info = {d: {"name": n} for d, n in profiles.items()}
    (ud / "Local State").write_text(json.dumps({"profile": {"info_cache": info}}), encoding="utf-8")
    for d in profiles:
        if d not in deleted:
            (ud / d).mkdir()
    return ud


class BrowserProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.missing = self.root / "nothing-here"
        # Only "brave" exists in these tests; the exe lookup is faked.
        self.dirs = {"brave": make_user_data(self.root / "brave", {"Default": "General", "Profile 7": "Work",
                                                                   "Profile 9": "Old"}, deleted=("Profile 9",)),
                     "chrome": self.missing, "edge": self.missing}
        self.exe = mock.patch.object(browsers, "_find_exe", return_value=r"C:\fake\brave.exe")
        self.exe.start()

    def tearDown(self):
        self.exe.stop()
        self.tmp.cleanup()

    def test_lists_profiles_with_their_names(self):
        ps = browsers.list_profiles(self.dirs)
        self.assertEqual([(p.id, p.label) for p in ps],
                         [("brave|Default", "Brave - General"), ("brave|Profile 7", "Brave - Work")])

    def test_deleted_profiles_and_missing_browsers_are_skipped(self):
        ids = [p.id for p in browsers.list_profiles(self.dirs)]
        self.assertNotIn("brave|Profile 9", ids)

    def test_open_url_uses_the_profile_directory(self):
        ps = browsers.list_profiles(self.dirs)
        with mock.patch.object(browsers, "list_profiles", return_value=ps), \
             mock.patch.object(browsers.subprocess, "Popen") as popen, \
             mock.patch.object(browsers.webbrowser, "open") as default:
            browsers.open_url("https://drive.google.com/", "brave|Profile 7")
        popen.assert_called_once_with([r"C:\fake\brave.exe", "--profile-directory=Profile 7",
                                       "https://drive.google.com/"])
        default.assert_not_called()

    def test_unknown_or_no_profile_falls_back_to_default_browser(self):
        with mock.patch.object(browsers, "list_profiles", return_value=[]), \
             mock.patch.object(browsers.subprocess, "Popen") as popen, \
             mock.patch.object(browsers.webbrowser, "open") as default:
            browsers.open_url("https://a/", "brave|Profile 99")
            browsers.open_url("https://b/", None)
        popen.assert_not_called()
        self.assertEqual([c.args[0] for c in default.call_args_list], ["https://a/", "https://b/"])

    def test_controller_routes_library_calls_to_the_profile(self):
        name = browsers.register_controller("brave|Profile 7")
        with mock.patch.object(browsers, "open_url") as open_url:
            webbrowser.get(name).open("https://accounts.google.com/", new=1, autoraise=True)
        open_url.assert_called_once_with("https://accounts.google.com/", "brave|Profile 7")


if __name__ == "__main__":
    unittest.main()
