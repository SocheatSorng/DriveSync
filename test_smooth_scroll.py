"""Tests for batched mouse-wheel scrolling.
Run:  python -m unittest test_smooth_scroll -v
"""
import tkinter as tk
import unittest

import smooth_scroll


class FakeCanvas:
    def __init__(self, view=(0.0, 0.5)):
        self.view, self.scrolls = view, []

    def yview(self, *args):
        if args:
            self.scrolls.append(("y", args[1]))
        return self.view

    def xview(self, *args):
        if args:
            self.scrolls.append(("x", args[1]))
        return self.view


class FakeFrame:
    """Just what queue_scroll uses: the canvas, and after() (run by hand here)."""

    def __init__(self, canvas):
        self._parent_canvas = canvas
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append(fn)
        return len(self.jobs)

    def run_next_frame(self):
        jobs, self.jobs = self.jobs, []
        for fn in jobs:
            fn()


class QueueScrollTests(unittest.TestCase):
    def test_first_notch_scrolls_at_once_and_the_rest_are_added_up(self):
        canvas = FakeCanvas()
        frame = FakeFrame(canvas)
        smooth_scroll.queue_scroll(frame, -120)            # one notch down: 20 units, right away
        self.assertEqual(canvas.scrolls, [("y", 20)])
        for _ in range(5):                                 # a fast flick while that repaints
            smooth_scroll.queue_scroll(frame, -120)
        self.assertEqual(canvas.scrolls, [("y", 20)])      # nothing more yet...
        frame.run_next_frame()
        self.assertEqual(canvas.scrolls, [("y", 20), ("y", 100)])   # ...then one scroll for all five
        frame.run_next_frame()                             # nothing left: no more scrolls or timers
        self.assertEqual(len(canvas.scrolls), 2)
        self.assertEqual(frame.jobs, [])

    def test_up_and_down_cancel_out(self):
        canvas = FakeCanvas()
        frame = FakeFrame(canvas)
        smooth_scroll.queue_scroll(frame, 120)
        smooth_scroll.queue_scroll(frame, 120)
        smooth_scroll.queue_scroll(frame, -120)
        frame.run_next_frame()
        self.assertEqual(canvas.scrolls, [("y", -20)])     # the 2nd and 3rd notches cancelled

    def test_horizontal_and_nothing_to_scroll(self):
        canvas = FakeCanvas()
        frame = FakeFrame(canvas)
        smooth_scroll.queue_scroll(frame, -120, horizontal=True)
        self.assertEqual(canvas.scrolls, [("x", 20)])
        fits = FakeCanvas(view=(0.0, 1.0))
        smooth_scroll.queue_scroll(FakeFrame(fits), -120)
        self.assertEqual(fits.scrolls, [])

    def test_closed_frame_is_ignored(self):
        canvas = FakeCanvas()
        frame = FakeFrame(canvas)
        smooth_scroll.queue_scroll(frame, -120)
        smooth_scroll.queue_scroll(frame, -120)

        def gone(*_args):
            raise tk.TclError("invalid command name")
        canvas.yview = gone
        frame.run_next_frame()                             # no error


if __name__ == "__main__":
    unittest.main()
