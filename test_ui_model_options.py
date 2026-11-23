"""Tests for the Settings options in ui_model: what the menus show and what gets saved.
Run:  python -m unittest test_ui_model_options -v
"""
import unittest

import ui_model as m

DEFAULTS = {"interval_minutes": 15, "drive_poll_seconds": 10, "speed_limit_kbps": 0, "watch_changes": True,
            "notify_on_changes": False, "high_priority": False, "pause_on_metered": False, "auto_update": True,
            "appearance": "System", "folders": []}


class OptionLabelTests(unittest.TestCase):
    def test_defaults(self):
        o = m.option_labels(DEFAULTS)
        self.assertEqual((o.interval, o.poll, o.limit, o.priority), ("Every 15 minutes", "Every 10 seconds",
                                                                     "No limit", "Normal"))
        self.assertEqual(o.intervals, list(m.INTERVALS))
        self.assertTrue(o.watch and o.auto_update)
        self.assertFalse(o.notify or o.metered)
        self.assertEqual(o.appearance, "System")

    def test_values_not_in_the_menus_still_show(self):
        o = m.option_labels({**DEFAULTS, "interval_minutes": 7, "speed_limit_kbps": 300, "high_priority": True})
        self.assertEqual(o.interval, "Every 7 minutes")
        self.assertEqual(o.intervals[0], "Every 7 minutes")
        self.assertEqual(o.limit, "300 KB/s")
        self.assertEqual(o.priority, "High")
        self.assertEqual(m.option_labels({**DEFAULTS, "drive_poll_seconds": 99}).poll, "Every 10 seconds")


class OptionsCfgTests(unittest.TestCase):
    def test_saves_the_same_keys_as_before(self):
        cfg = m.options_cfg(DEFAULTS, interval="Every hour", poll="Every minute", limit="2 MB/s", priority="High",
                            watch=False, notify=True, metered=True, auto_update=False)
        self.assertEqual({k: cfg[k] for k in ("interval_minutes", "drive_poll_seconds", "speed_limit_kbps",
                                             "high_priority", "watch_changes", "notify_on_changes",
                                             "pause_on_metered", "auto_update")},
                         {"interval_minutes": 60, "drive_poll_seconds": 60, "speed_limit_kbps": 2048,
                          "high_priority": True, "watch_changes": False, "notify_on_changes": True,
                          "pause_on_metered": True, "auto_update": False})
        self.assertEqual(cfg["folders"], [])                      # everything else kept

    def test_labels_round_trip(self):
        o = m.option_labels(DEFAULTS)
        cfg = m.options_cfg(DEFAULTS, interval=o.interval, poll=o.poll, limit=o.limit, priority=o.priority,
                            watch=o.watch, notify=o.notify, metered=o.metered, auto_update=o.auto_update)
        self.assertEqual(cfg, DEFAULTS)

    def test_unknown_labels_keep_the_old_value(self):
        cfg = m.options_cfg({**DEFAULTS, "interval_minutes": 7, "speed_limit_kbps": 300},
                            interval="Every 7 minutes", poll="?", limit="300 KB/s", priority="Normal",
                            watch=True, notify=False, metered=False, auto_update=True)
        self.assertEqual((cfg["interval_minutes"], cfg["speed_limit_kbps"], cfg["drive_poll_seconds"]), (7, 300, 10))


if __name__ == "__main__":
    unittest.main()
