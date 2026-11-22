"""The Activity tab: sync runs as cards, grouped by day, with filters, search and details."""
from __future__ import annotations

import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

import activity

MUTED = ("#5f6368", "#9aa0a6")
CARD = ("#f1f3f4", "#2b2b2b")
LINK = ("#1a73e8", "#8ab4f8")
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}
RED, AMBER, GREEN, BLUE, GRAY = "#d93025", "#e37400", "#1e8e3e", "#1a73e8", "#80868b"
BADGES = {  # kind: (label, color)
    "error": ("Problem", RED), "conflict": ("Conflict", AMBER), "skipped": ("Skipped", AMBER),
    "uploaded": ("Uploaded", GREEN), "updated": ("Updated", BLUE), "moved": ("Moved", BLUE),
    "downloaded": ("Downloaded", "#00838f"),
    "deleted_local": ("Recycle Bin", GRAY), "trashed": ("Drive trash", GRAY), "folder": ("New folder", "#9334e6"),
}
ORDER = list(BADGES)  # problems first in the details list
PAGE = 40             # cards rendered at a time ("Show older" loads more)
DETAILS = 200         # file rows shown per card


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def entry_id(e: dict) -> str:
    return f"{e['ts']}|{e.get('folder', e.get('message', ''))}"


class ActivityView:
    def __init__(self, parent, app):
        self.app = app
        self.entries: list[dict] = app.activity.load()   # oldest first
        self.view, self.query, self.limit = "All", "", PAGE
        self.expanded: set[str] = set()
        self.collapsed: set[str] = set()                  # problem cards the user closed
        self._search_job = None

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(4, 2))
        self.filter = ctk.CTkSegmentedButton(bar, values=["All", "Changes", "Problems"], command=self.set_view)
        self.filter.set("All")
        self.filter.pack(side="left")
        self.search = ctk.CTkEntry(bar, placeholder_text="Search files, folders, accounts…", width=230)
        self.search.pack(side="left", padx=8)
        self.search.bind("<KeyRelease>", self._on_search)
        ctk.CTkButton(bar, text="Log file", width=80, command=app.open_log, **SECONDARY).pack(side="right")
        ctk.CTkButton(bar, text="Clear", width=64, command=self.clear, **SECONDARY).pack(side="right", padx=(0, 6))

        self.header = ctk.CTkLabel(parent, text="", font=font(12), text_color=MUTED, anchor="w", justify="left")
        self.header.pack(fill="x", padx=8, pady=(2, 4))
        self.list = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.list.pack(fill="both", expand=True)
        self.refresh_header()
        self.render()

    # ---------------------------------------------------------------- data
    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        if len(self.entries) > activity.KEEP:
            del self.entries[: len(self.entries) - activity.KEEP]
        if activity.matches(entry, self.view, self.query):
            self.render()

    def clear(self) -> None:
        if not self.entries:
            return
        if messagebox.askyesno("Drive Sync", "Clear the activity history?\n\nThis only clears the list. "
                               "Your files and the log file are not affected.", parent=self.list):
            self.app.activity.clear()
            self.entries.clear()
            self.render()

    # ------------------------------------------------------------ controls
    def set_view(self, view: str) -> None:
        self.view, self.limit = view, PAGE
        self.render()

    def _on_search(self, _event=None) -> None:
        if self._search_job:
            self.list.after_cancel(self._search_job)
        self._search_job = self.list.after(250, self._apply_search)

    def _apply_search(self) -> None:
        self._search_job = None
        query = self.search.get()
        if query != self.query:
            self.query, self.limit = query, PAGE
            self.render()

    def refresh_header(self) -> None:
        lc = self.app.last_check
        problems = sum(1 for e in self.entries[-200:] if activity.has_problems(e))
        if not lc:
            text = "Not checked yet."
        else:
            when = time.strftime("%H:%M", time.localtime(lc["ts"]))
            n = lc["folders"]
            result = (f"{lc['errors']} problem{'s' if lc['errors'] != 1 else ''}" if lc["errors"]
                      else "no changes" if not lc["changes"]
                      else f"{lc['changes']} change{'s' if lc['changes'] != 1 else ''}")
            text = (f"Last checked {when} ({activity.TRIGGERS.get(lc['trigger'], lc['trigger'])}) · "
                    f"{n} folder{'s' if n != 1 else ''} · {result}")
        hint = "  ·  Checks that changed nothing aren't listed."
        self.header.configure(text=text + hint + (f"  ·  {problems} recent problem(s) - see Problems"
                                                  if problems and self.view != "Problems" else ""))

    # ------------------------------------------------------------- render
    def render(self) -> None:
        for w in self.list.winfo_children():
            w.destroy()
        self.refresh_header()
        shown = [e for e in reversed(self.entries) if activity.matches(e, self.view, self.query)]
        if not shown:
            self._empty()
            return
        day = None
        for e in shown[: self.limit]:
            label = activity.day_label(e["ts"])
            if label != day:
                day = label
                ctk.CTkLabel(self.list, text=label.upper(), font=font(11, "bold"), text_color=MUTED,
                             anchor="w").pack(fill="x", padx=6, pady=(10 if day else 0, 4))
            (self._run_card if e["type"] == "run" else self._event_card)(e)
        rest = len(shown) - self.limit
        if rest > 0:
            ctk.CTkButton(self.list, text=f"Show older activity ({rest} more)", **SECONDARY,
                          command=self._more).pack(pady=10)

    def _more(self) -> None:
        self.limit += PAGE
        self.render()

    def _empty(self) -> None:
        box = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=10)
        box.pack(fill="x", padx=4, pady=24)
        if not self.entries:
            title, text = "No activity yet", ("Uploads, updates and problems will appear here, newest first, "
                                               "grouped by day.")
        elif self.query:
            title, text = "Nothing found", f"No activity matches “{self.query}”."
        elif self.view == "Problems":
            title, text = "No problems", "Everything synced without errors."
        else:
            title, text = "Nothing to show", "No activity matches this filter."
        ctk.CTkLabel(box, text=title, font=font(14, "bold")).pack(pady=(18, 2))
        ctk.CTkLabel(box, text=text, font=font(12), text_color=MUTED, wraplength=520).pack(pady=(0, 18))

    def _card(self, color: str, title: str, subtitle: str, ts: float):
        c = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=10)
        c.pack(fill="x", padx=4, pady=(0, 6))
        c.columnconfigure(1, weight=1)
        ctk.CTkLabel(c, text="●", font=font(15), width=18, text_color=color).grid(
            row=0, column=0, rowspan=2, padx=(12, 6), pady=(10, 0), sticky="n")
        ctk.CTkLabel(c, text=title, font=font(13, "bold"), anchor="w", justify="left", wraplength=520).grid(
            row=0, column=1, sticky="w", pady=(9, 0))
        ctk.CTkLabel(c, text=time.strftime("%H:%M", time.localtime(ts)), font=font(12), text_color=MUTED).grid(
            row=0, column=2, padx=12, pady=(9, 0), sticky="ne")
        ctk.CTkLabel(c, text=subtitle, font=font(12), text_color=MUTED, anchor="w", justify="left",
                     wraplength=560).grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 9))
        return c

    def _event_card(self, e: dict) -> None:
        color = {"error": RED, "warning": AMBER}.get(e.get("level"), BLUE)
        self._card(color, e.get("message", ""), e.get("detail", ""), e["ts"])

    def _run_card(self, e: dict) -> None:
        c = e["counts"]
        color = RED if c.get("error") else AMBER if c.get("skipped") or c.get("conflict") else GREEN
        name = Path(e["folder"]).name or e["folder"]
        bits = [activity.summary(e), activity.TRIGGERS.get(e.get("trigger"), "")]
        if e.get("account"):
            bits.append(e["account"])
        card = self._card(color, name, " · ".join(b for b in bits if b), e["ts"])

        n = len(e["items"]) + e.get("more", 0)
        if not n:
            return
        eid = entry_id(e)
        auto_open = bool(c.get("error")) and eid not in self.collapsed
        is_open = eid in self.expanded or auto_open
        toggle = ctk.CTkButton(card, text=("Hide details ▴" if is_open else f"Show {n} item{'s' if n != 1 else ''} ▾"),
                               width=10, height=24, font=font(12), fg_color="transparent", text_color=LINK,
                               hover_color=("#e8f0fe", "#283142"), anchor="w",
                               command=lambda: self._toggle(eid, is_open))
        toggle.grid(row=2, column=1, sticky="w", pady=(0, 6))
        if is_open:
            self._details(card, e)

    def _toggle(self, eid: str, is_open: bool) -> None:
        if is_open:
            self.expanded.discard(eid)
            self.collapsed.add(eid)
        else:
            self.expanded.add(eid)
            self.collapsed.discard(eid)
        self.render()

    def _details(self, card, e: dict) -> None:
        box = ctk.CTkFrame(card, fg_color=("#ffffff", "#333333"), corner_radius=8)
        box.grid(row=3, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 12))
        box.columnconfigure(1, weight=1)
        items = sorted(e["items"], key=lambda i: ORDER.index(i["kind"]) if i["kind"] in ORDER else 99)
        q = self.query.strip().lower()
        if q:  # when searching, show matching files first
            items.sort(key=lambda i: q not in i["path"].lower())
        row = 0
        for it in items[:DETAILS]:
            label, color = BADGES.get(it["kind"], (it["kind"], GRAY))
            ctk.CTkLabel(box, text=label, font=font(11, "bold"), text_color="white", fg_color=color,
                         corner_radius=6, width=78, height=20).grid(
                row=row, column=0, padx=(10, 8), pady=3, sticky="nw")
            path = it["path"] if it["path"] != "." else "(whole folder)"
            ctk.CTkLabel(box, text=path, font=font(12), anchor="w", justify="left", wraplength=480).grid(
                row=row, column=1, sticky="w", pady=3)
            row += 1
            if it.get("message"):
                ctk.CTkLabel(box, text=it["message"], font=font(11), text_color=RED if it["kind"] == "error"
                             else MUTED, anchor="w", justify="left", wraplength=480).grid(
                    row=row, column=1, sticky="w", pady=(0, 4))
                row += 1
        hidden = max(0, len(items) - DETAILS) + e.get("more", 0)
        if hidden:
            ctk.CTkLabel(box, text=f"…and {hidden} more (full list in the log file)", font=font(11),
                         text_color=MUTED, anchor="w").grid(row=row, column=1, sticky="w", pady=(2, 6))
