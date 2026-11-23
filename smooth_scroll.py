"""Smoother mouse-wheel scrolling for CustomTkinter's scrollable frames (Windows).

CTkScrollableFrame scrolls on every wheel event. Every row in it is a separate Windows control that
must repaint after each scroll, and a fast flick sends events quicker than that: the list fell
behind and smeared old rows across the screen. Here the first notch scrolls at once, and the
notches that arrive during the next frame (~16 ms) are added up into one scroll, so Windows can
repaint in between.

install() must run before any CTkScrollableFrame is made (each binds its wheel handler when made).
"""
from __future__ import annotations

import sys
import tkinter as tk

import customtkinter as ctk

FRAME_MS = 16                       # one scroll per screen refresh at most
_original = ctk.CTkScrollableFrame._mouse_wheel_all


def queue_scroll(frame: ctk.CTkScrollableFrame, delta: int, horizontal: bool = False) -> None:
    """Scroll this frame by a wheel event's delta: now if it's idle, else with the next frame."""
    canvas = frame._parent_canvas
    view = canvas.xview() if horizontal else canvas.yview()
    if view == (0.0, 1.0):
        return                      # everything fits: nothing to scroll
    state = frame.__dict__.setdefault("_wheel", {"x": 0.0, "y": 0.0, "job": None})
    state["x" if horizontal else "y"] += -delta / 6     # what CTk scrolls per event (in canvas units)
    if state["job"] is None:
        _flush(frame)               # the first notch moves straight away


def _flush(frame: ctk.CTkScrollableFrame) -> None:
    state = frame._wheel
    state["job"] = None
    moved = False
    try:
        canvas = frame._parent_canvas
        for key, view in (("x", canvas.xview), ("y", canvas.yview)):
            units = int(state[key])
            if units:
                view("scroll", units, "units")
                state[key] -= units
                moved = True
        if moved:   # gather what arrives while this scroll repaints, then scroll once more
            state["job"] = frame.after(FRAME_MS, lambda: _flush(frame))
    except tk.TclError:             # the frame was closed meanwhile
        pass


def _mouse_wheel_all(self, event) -> None:
    if not sys.platform.startswith("win"):
        return _original(self, event)
    if self._check_if_valid_scroll(event.widget):
        queue_scroll(self, event.delta, horizontal=self._shift_pressed)


def install() -> None:
    ctk.CTkScrollableFrame._mouse_wheel_all = _mouse_wheel_all
