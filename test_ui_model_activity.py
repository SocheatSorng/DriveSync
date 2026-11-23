"""Tests for the Activity tab's texts and rules in ui_model (shared by the Tk and Qt windows).
Run:  python -m unittest test_ui_model_activity -v
"""
import time
import unittest

import ui_model as m

NOW = time.mktime((2026, 11, 23, 17, 51, 0, 0, 0, -1))


def run(items=(), counts=None, **kw):
    items = list(items)
    if counts is None:
        counts = {}
        for it in items:
            counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return {"type": "run", "ts": NOW, "folder": r"D:\Storage\Image", "trigger": "startup",
            "account": "teacher@example.com", "items": items, "counts": counts, **kw}


class ActivityHeaderTests(unittest.TestCase):
    def test_not_checked(self):
        self.assertEqual(m.activity_header(None, [], "All"),
                         "Not checked yet.  ·  Checks that changed nothing aren't listed.")

    def test_last_check_and_problems(self):
        lc = {"ts": NOW, "folders": 2, "errors": 0, "changes": 3, "trigger": "manual"}
        bad = run([{"kind": "error", "path": "x"}])
        text = m.activity_header(lc, [bad], "All")
        self.assertEqual(text, "Last checked 17:51 (Sync now) · 2 folders · 3 changes  ·  Checks that changed "
                               "nothing aren't listed.  ·  1 recent problem(s) - see Problems")
        self.assertNotIn("see Problems", m.activity_header(lc, [bad], "Problems"))
        lc1 = {**lc, "folders": 1, "changes": 0}
        self.assertIn("1 folder · no changes", m.activity_header(lc1, [], "All"))
        lc2 = {**lc, "errors": 1}
        self.assertIn("· 1 problem", m.activity_header(lc2, [], "All"))


class ActivityEmptyTests(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(m.activity_empty(False, "", "All")[0], "No activity yet")
        self.assertEqual(m.activity_empty(True, "cat", "All"), ("Nothing found", "No activity matches “cat”."))
        self.assertEqual(m.activity_empty(True, "", "Problems")[0], "No problems")
        self.assertEqual(m.activity_empty(True, "", "Changes")[0], "Nothing to show")


class CardTests(unittest.TestCase):
    def test_run_card(self):
        e = run([{"kind": "uploaded", "path": "a"}, {"kind": "uploaded", "path": "b"}])
        c = m.run_card(e)
        self.assertEqual((c.title, c.dot, c.items), ("Image", "ok", 2))
        self.assertEqual(c.subtitle, "2 uploaded · when Drive Sync started · teacher@example.com")
        self.assertEqual(c.when, "17:51")
        self.assertEqual(m.run_card(run([{"kind": "error", "path": "x"}])).dot, "error")
        self.assertEqual(m.run_card(run([{"kind": "conflict", "path": "x"}])).dot, "warning")
        self.assertEqual(m.run_card(run([], more=0)).items, 0)
        self.assertEqual(m.run_card(run([{"kind": "uploaded", "path": "a"}], more=4)).items, 5)

    def test_event_card(self):
        e = {"type": "event", "ts": NOW, "level": "warning", "message": "Sign-in didn't finish", "detail": "x"}
        c = m.event_card(e)
        self.assertEqual((c.title, c.subtitle, c.when, c.dot), ("Sign-in didn't finish", "x", "17:51", "warning"))
        self.assertEqual(m.event_card({**e, "level": "info"}).dot, "info")
        self.assertEqual(m.event_card({**e, "level": "error"}).dot, "error")

    def test_toggle_text(self):
        self.assertEqual(m.details_toggle(True, 3), "Hide details ▴")
        self.assertEqual(m.details_toggle(False, 1), "Show 1 item ▾")
        self.assertEqual(m.details_toggle(False, 7), "Show 7 items ▾")

    def test_problem_runs_open_by_themselves_until_closed(self):
        bad = run([{"kind": "error", "path": "x"}])
        good = run([{"kind": "uploaded", "path": "x"}])
        eid = m.entry_id(bad)
        self.assertTrue(m.details_open(bad, set(), set()))
        self.assertFalse(m.details_open(bad, set(), {eid}))
        self.assertFalse(m.details_open(good, set(), set()))
        self.assertTrue(m.details_open(good, {m.entry_id(good)}, set()))


class DetailRowsTests(unittest.TestCase):
    def test_problems_first_then_search_matches_and_a_limit(self):
        items = [{"kind": "uploaded", "path": f"Photos/IMG_{i}.jpg"} for i in range(250)]
        items.append({"kind": "error", "path": "Micomet", "message": "Couldn't read folder"})
        shown, times, hidden = m.detail_rows(run(items, more=7), "")
        self.assertEqual(shown[0]["kind"], "error")
        self.assertEqual(len(shown), m.DETAILS)
        self.assertEqual(len(times), m.DETAILS)
        self.assertEqual(hidden, 251 - m.DETAILS + 7)
        shown, _t, _h = m.detail_rows(run(items), "img_249")
        self.assertEqual(shown[0]["path"], "Photos/IMG_249.jpg")    # searching: matching files first

    def test_badges(self):
        self.assertEqual(m.badge("error"), ("Problem", "#d93025"))
        self.assertEqual(m.badge("downloaded"), ("Downloaded", "#00838f"))
        self.assertEqual(m.badge("renamed")[0], "renamed")          # unknown kinds still show


if __name__ == "__main__":
    unittest.main()
