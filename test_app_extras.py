"""Tests for timed pauses, Drive storage numbers and metered connections.
Run:  python -m unittest test_app_extras -v
"""
import time
import unittest
from unittest import mock

import metered
import tray_app

GB = 1024 ** 3


def bare_app():
    """An App with only what these features use (no window, no tray icon, no worker)."""
    app = tray_app.App.__new__(tray_app.App)
    app.paused, app.pause_until, app.metered_held, app.animating = False, None, False, False
    app.progress = None
    app.state, app.status, app.detail = "idle", "", ""
    app.states, app.notes = [], []
    app.set_state = lambda *a: app.states.append(a)
    app.notify = app.notes.append
    app.wake = mock.Mock()
    app.ui = mock.Mock()
    app.quota, app._quota_warned = {}, set()
    app.cfg = {}
    return app


class PauseTests(unittest.TestCase):
    def test_timed_pause_and_label(self):
        app = bare_app()
        app.pause(3600)
        self.assertTrue(app.paused)
        self.assertAlmostEqual(app.pause_until, time.time() + 3600, delta=2)
        self.assertTrue(app.states[-1][1].startswith("Paused until "))
        app.resume()
        self.assertEqual((app.paused, app.pause_until), (False, None))

    def test_pause_until_resumed(self):
        app = bare_app()
        app.toggle_pause()
        self.assertEqual((app.paused, app.pause_until, app.states[-1][1]), (True, None, "Paused"))
        app.toggle_pause()
        self.assertFalse(app.paused)

    def test_seconds_until_morning(self):
        evening = time.mktime((2026, 11, 22, 21, 30, 0, 0, 0, -1))
        self.assertEqual(tray_app.seconds_until(7, evening), 9.5 * 3600)
        early = time.mktime((2026, 11, 22, 5, 0, 0, 0, 0, -1))
        self.assertEqual(tray_app.seconds_until(7, early), 2 * 3600)


class QuotaTests(unittest.TestCase):
    def drive(self, usage, limit=15 * GB):
        d = mock.Mock()
        d.storage_quota.return_value = {"limit": limit, "usage": usage}
        return d

    def test_text(self):
        self.assertEqual(tray_app.quota_text({"limit": 15 * GB, "usage": 12 * GB}),
                         "12.0 GB of 15.0 GB used (80%)")
        self.assertEqual(tray_app.quota_text({"limit": None, "usage": GB}), "1.0 GB used")
        self.assertEqual(tray_app.quota_text(None), "")

    def test_nearly_full_warns_once_and_sets_free_space(self):
        app = bare_app()
        d = self.drive(14 * GB)
        app._update_quota("a@example.com", d)
        app._update_quota("a@example.com", d)   # reused numbers, no second warning
        self.assertEqual(len(app.notes), 1)
        self.assertEqual(d.storage_quota.call_count, 1)
        self.assertEqual(d.quota_free, GB)

    def test_unlimited_has_no_precheck(self):
        app = bare_app()
        d = self.drive(5 * GB, limit=None)
        app._update_quota("a@example.com", d)
        self.assertIsNone(d.quota_free)
        self.assertEqual(app.notes, [])


class AnimationTests(unittest.TestCase):
    def test_spins_only_while_files_move(self):
        from progress import Progress
        app = bare_app()
        app.state, app.progress = "syncing", Progress()
        self.assertFalse(app.should_spin())             # comparing files: still icon
        app.progress.transfer("upload", "a.wav", 100)
        self.assertTrue(app.should_spin())
        app.paused = True
        self.assertFalse(app.should_spin())
        app.paused, app.state = False, "idle"
        self.assertFalse(app.should_spin())

    def test_frames_are_drawn_for_the_tray(self):
        self.assertEqual(len(tray_app.SYNC_FRAMES), 8)
        self.assertEqual(tray_app.SYNC_FRAMES[0].size, tray_app.ICONS["syncing"].size)
        self.assertNotEqual(tray_app.SYNC_FRAMES[0].tobytes(), tray_app.SYNC_FRAMES[2].tobytes())


class MeteredTests(unittest.TestCase):
    def test_parse(self):
        self.assertFalse(metered.parse("Unrestricted False False"))
        self.assertTrue(metered.parse("Fixed False False"))
        self.assertTrue(metered.parse("Variable False False"))
        self.assertTrue(metered.parse("Unrestricted True False"))     # roaming
        self.assertFalse(metered.parse("none"))
        self.assertFalse(metered.parse(""))

    def test_answer_is_reused_for_a_minute(self):
        with mock.patch.object(metered, "_ask_windows", return_value=True) as ask, \
                mock.patch.object(metered, "_cache", None):
            self.assertTrue(metered.is_metered(now=1000))
            self.assertTrue(metered.is_metered(now=1030))
            self.assertEqual(ask.call_count, 1)
            metered.is_metered(now=1000 + metered.CHECK_EVERY + 1)
            self.assertEqual(ask.call_count, 2)

    def test_hold_only_when_setting_is_on(self):
        app = bare_app()
        with mock.patch.object(metered, "is_metered", return_value=True):
            self.assertFalse(app._metered_hold())
            app.cfg = {"pause_on_metered": True}
            self.assertTrue(app._metered_hold())
            app._check_metered()
            self.assertEqual((app.metered_held, app.states[-1][1]), (True, tray_app.METERED_STATUS))


if __name__ == "__main__":
    unittest.main()
