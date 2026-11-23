"""Tests for the Activity tab's file lists.
Run:  python -m unittest test_activity_view -v
"""
import time
import tkinter as tk
import unittest

import customtkinter as ctk

import activity_view


def item(kind, path, message=""):
    return {"kind": kind, "path": path, "message": message}


class DetailLinesTests(unittest.TestCase):
    def test_badge_path_time_and_message(self):
        lines = activity_view.detail_lines(
            [item("error", "Micomet", "Couldn't read folder"), item("downloaded", ".")], ["16:45", ""], 0)
        self.assertEqual(lines, [
            (" Problem ", ("row", "badge-error")), ("\tMicomet", ("row",)), ("   16:45", ("row", "muted")),
            ("\n", ("row",)), ("Couldn't read folder\n", ("note", "red")),
            (" Downloaded ", ("row", "badge-downloaded")), ("\t(whole folder)", ("row",)), ("\n", ("row",)),
        ])

    def test_unknown_kind_and_hidden_count(self):
        lines = activity_view.detail_lines([item("renamed", "a.txt", "why")], [""], 7)
        self.assertEqual(lines[0], (" renamed ", ("row", "badge-other")))
        self.assertIn(("why\n", ("note", "muted")), lines)
        self.assertEqual(lines[-1], ("…and 7 more (full list in the log file)\n", ("note", "muted")))


class DetailsWidgetTests(unittest.TestCase):
    """Each Tk widget is a Windows control that moves with the window: a long file list must not
    make hundreds of them (dragging the window lagged with ~3,000)."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ctk.CTk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"no display: {e}") from e
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def count(self, w):
        return 1 + sum(self.count(c) for c in w.winfo_children())

    def test_two_hundred_files_are_one_text_box(self):
        frame = ctk.CTkScrollableFrame(self.root)
        view = activity_view.ActivityView.__new__(activity_view.ActivityView)
        view.query = ""
        card = ctk.CTkFrame(frame)
        items = [item("downloaded", f"Photos/IMG_{i:04d}.jpg") for i in range(200)]
        view._details(card, {"ts": time.time(), "items": items, "more": 0})
        self.assertLess(self.count(card), 15)
        box = card.winfo_children()[0]
        text = next(c for c in box.winfo_children() if isinstance(c, tk.Text))
        content = text.get("1.0", "end")
        self.assertIn("Photos/IMG_0000.jpg", content)
        self.assertIn("Photos/IMG_0199.jpg", content)
        self.assertEqual(str(text.cget("state")), "disabled")
        frame.destroy()


if __name__ == "__main__":
    unittest.main()
