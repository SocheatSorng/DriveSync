"""Tests for timed pauses, Drive storage numbers and metered connections.
Run:  python -m unittest test_app_extras -v
"""
import threading
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
    app.sync_running, app.spin_until = False, 0.0
    app.icon_lock, app.anim_wake = threading.RLock(), threading.Event()
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


class FakeAnim:
    def __init__(self):
        self.shown = []

    def show(self, i):
        self.shown.append(i)


class AnimationTests(unittest.TestCase):
    def syncing_app(self):
        app = bare_app()
        app._sync_began()
        app.state = "syncing"
        return app

    def test_spins_for_the_whole_sync_not_only_transfers(self):
        app = self.syncing_app()
        self.assertTrue(app.should_spin())              # comparing / trashing - no file moving yet
        self.assertTrue(app.anim_wake.is_set())         # the animation thread is woken at once
        later = time.monotonic() + 60
        self.assertTrue(app.should_spin(later))         # a long sync: still running
        app._sync_ended()
        app.state = "idle"
        self.assertFalse(app.should_spin(later))

    def test_quick_sync_still_shows_one_whole_lap(self):
        app = self.syncing_app()
        start = time.monotonic()
        app._sync_ended()                               # done in well under a second
        app.state = "idle"
        self.assertTrue(app.should_spin(start))
        self.assertFalse(app.should_spin(start + tray_app.SPIN_MIN_SECONDS + 0.01))

    def test_back_to_back_syncs_keep_spinning(self):
        app = self.syncing_app()
        app._sync_ended()
        app.state = "idle"
        app._sync_began()
        self.assertTrue(app.should_spin(time.monotonic() + 60))

    def test_pause_stops_the_light_and_resume_brings_it_back(self):
        app = self.syncing_app()
        app.pause(None)
        self.assertFalse(app.should_spin())
        app.resume()                                    # the sync is still finishing its file
        self.assertTrue(app.should_spin())
        app.pause(None)
        app._sync_ended()                               # stopped while paused
        app.resume()
        self.assertFalse(app.should_spin())             # no left-over lap after resuming

    def test_no_light_when_metered_or_error(self):
        app = self.syncing_app()
        app.metered_held = True
        self.assertFalse(app.should_spin())
        app.metered_held, app.state = False, "error"   # the red icon isn't hidden by the light
        self.assertFalse(app.should_spin())

    def test_sync_all_marks_the_round_however_it_ends(self):
        app = bare_app()
        seen = []
        app._sync_all = lambda **kw: seen.append(app.sync_running) or True
        self.assertTrue(app.sync_all(trigger="drive"))
        self.assertEqual(seen, [True])
        self.assertFalse(app.sync_running)
        app._sync_all = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertFalse(app.sync_all())
        self.assertFalse(app.sync_running)
        self.assertEqual(app.states[-1][0], "error")

    def test_tick_shows_frames_then_the_still_icon(self):
        app = self.syncing_app()
        app.icon, anim = mock.Mock(), FakeAnim()
        self.assertTrue(app._animation_tick(anim, True))
        self.assertTrue(app._animation_tick(anim, True))
        self.assertEqual(anim.shown, [0, 1])
        self.assertTrue(app.animating)
        app._sync_ended()
        app.state, app.spin_until = "idle", 0.0
        self.assertFalse(app._animation_tick(anim, True))
        self.assertFalse(app.animating)
        self.assertIs(app.icon.icon, tray_app.ICONS["idle"])
        app._sync_began()
        app.state = "syncing"
        app._animation_tick(anim, True)
        self.assertEqual(anim.shown[-1], 0)             # each sync starts the lap from the top
        self.assertFalse(app._animation_tick(anim, False))   # Windows animations turned off
        self.assertIs(app.icon.icon, tray_app.ICONS["syncing"])

    def test_set_state_does_not_cover_a_running_light(self):
        app = self.syncing_app()
        app.icon, app.animating = mock.Mock(), True
        app.icon.icon = "frame"
        tray_app.App.set_state(app, "syncing", "Syncing… 5%")
        self.assertEqual(app.icon.icon, "frame")
        app.animating = False
        tray_app.App.set_state(app, "idle", "Up to date")
        self.assertIs(app.icon.icon, tray_app.ICONS["idle"])

    def test_frames_are_drawn_for_the_tray(self):
        self.assertEqual(len(tray_app.SYNC_FRAMES), 20)
        self.assertEqual(tray_app.SYNC_FRAMES[0].size, tray_app.ICONS["syncing"].size)
        self.assertNotEqual(tray_app.SYNC_FRAMES[0].tobytes(), tray_app.SYNC_FRAMES[2].tobytes())

    def test_syncing_shows_the_full_color_logo_not_amber(self):
        # the light covers only part of the outline, so most of the logo matches the idle icon
        idle, frame = tray_app.ICONS["idle"].tobytes(), tray_app.SYNC_FRAMES[5].tobytes()
        same = sum(a == b for a, b in zip(idle[::4], frame[::4])) / (len(idle) // 4)
        self.assertGreater(same, 0.5)


class RemoveDataTests(unittest.TestCase):
    def test_remove_data_keeps_everything_else(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "DriveSync"
            (base / "accounts").mkdir(parents=True)
            (base / "accounts" / "me@example.com.json").write_text("{}")
            (base / "state").mkdir()
            (base / "config.json").write_text("{}")
            (base / "tray_app.py").write_text("# a copy run from source keeps its code")
            removed = tray_app.remove_data(base, revoke=False)
            self.assertEqual(sorted(removed), ["accounts", "config.json", "state"])
            self.assertEqual([p.name for p in base.iterdir()], ["tray_app.py"])

    def test_empty_folder_is_removed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "DriveSync"
            (base / "logs").mkdir(parents=True)
            tray_app.remove_data(base, revoke=False)
            self.assertFalse(base.exists())


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


class SignInFailureTextTests(unittest.TestCase):
    def test_timeout_explains_the_usual_google_error_pages(self):
        text = tray_app.sign_in_failure_text(Exception("Timed out waiting for response from authorization server"))
        self.assertIn("Internal", text)
        self.assertIn("Test users", text)

    def test_other_errors_keep_the_details(self):
        text = tray_app.sign_in_failure_text(Exception("access_denied"))
        self.assertIn("Details: access_denied", text)

    def test_disabled_drive_api_links_to_the_enable_page(self):
        text = tray_app.sign_in_failure_text(Exception(
            "<HttpError 403 ... returned \"Google Drive API has not been used in project 1091363161830 before "
            "or it is disabled.\" reason: accessNotConfigured>"))
        self.assertIn("Drive API is turned off", text)
        self.assertIn("drive.googleapis.com/overview?project=1091363161830", text)


class CancelSignInAppTests(unittest.TestCase):
    def _app(self):
        app = bare_app()
        app.signing_in, app.signin_port, app.signin_cancelled = None, 0, False
        app.record_event = mock.Mock()
        return app

    def test_cancel_ends_the_wait_without_an_error_message(self):
        app = self._app()
        app.signing_in, app.signin_port = "new", 12345
        with mock.patch.object(tray_app.accounts, "cancel_sign_in") as cancel:
            app.cancel_sign_in()
            app.cancel_sign_in()                     # pressed twice: still one cancel
            deadline = time.monotonic() + 2
            while not cancel.called and time.monotonic() < deadline:
                time.sleep(0.01)
        cancel.assert_called_once_with(12345)
        with mock.patch.object(tray_app.accounts, "sign_in", side_effect=Exception("cancelled")):
            app._sign_in_thread(None)
        sent = [c.args[0] for c in app.ui.put.call_args_list]
        self.assertNotIn("error", [m[0] for m in sent])
        self.assertIsNone(app.signing_in)
        app.record_event.assert_not_called()

    def test_cancel_when_not_signing_in_does_nothing(self):
        app = self._app()
        with mock.patch.object(tray_app.accounts, "cancel_sign_in") as cancel:
            app.cancel_sign_in()
        cancel.assert_not_called()

    def test_a_real_failure_still_shows_an_error(self):
        app = self._app()
        app.signing_in = "new"
        with mock.patch.object(tray_app.accounts, "sign_in", side_effect=Exception("access_denied")):
            app._sign_in_thread(None)
        self.assertIn("error", [c.args[0][0] for c in app.ui.put.call_args_list])


if __name__ == "__main__":
    unittest.main()
