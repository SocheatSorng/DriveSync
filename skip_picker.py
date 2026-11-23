"""Choose what a synced folder skips: tick common items, or files and folders in a tree that shows both
the PC folder and its Google Drive folder (so things only on Drive can be kept off this PC).

The result is the same list of patterns the "Skip files or folders matching" box holds: a name like
`node_modules` (skipped anywhere) or a path inside the synced folder like `Project/.venv` (just that one).
A skipped item isn't synced either way: not downloaded, not uploaded, and left alone on both sides.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

MUTED = ("#5f6368", "#9aa0a6")
CARD = ("#f1f3f4", "#2b2b2b")
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}
# The plain helpers live in skip_model (no Tk needed for them); kept here under their old names.
from skip_model import (  # noqa: E402, F401
    COMMON,
    INHERITED,
    MAX_ROWS,
    OFF,
    ON,
    ONLY_DRIVE,
    describe,
    drive_lister,
    is_rule,
    pattern_for,
    split_patterns,
)


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


class SkipPicker(ctk.CTkToplevel):
    """self.result = the chosen list of patterns, or None if cancelled.

    drive_list: optional list(folder_id) (see drive_lister) - adds what's on Google Drive to the tree."""

    def __init__(self, parent, local: str, patterns: list[str], always: list[str] = (),
                 drive_list=None, drive_label: str = "", where: dict | None = None):
        super().__init__(parent)
        self.title("Choose what to skip")
        self.geometry("620x640")
        self.minsize(480, 460)
        self.transient(parent)
        self.result = None
        self.result_where: dict[str, str] = {}        # pattern -> "pc" / "drive" / "both" (where it is)
        self.where_before = dict(where or {})
        self.on_pc: set[str] = set()                  # paths seen on this PC
        self.on_drive: set[str] = set()               # paths seen on Google Drive
        self.drive_done: set[str] = set()             # folders whose Drive side has loaded
        self.local = local
        self.always = list(always)                    # built-in junk patterns: shown as skipped, not returned
        common_names = {p for p, _ in COMMON}
        self.common = {p: tk.BooleanVar(value=p in patterns) for p in common_names}
        self.paths: set[str] = set()                  # patterns for ticked tree items
        self.other: list[str] = []                    # typed patterns the picker has no tick box for
        self.rel_of: dict[str, str] = {}              # tree item id -> path inside the synced folder
        self.item_of: dict[str, str] = {}             # and back
        self.loaded: set[str] = set()                 # folders whose contents are in the tree
        self.drive_list = drive_list
        self.drive_ids: dict[str, str | None] = {"": None}   # folder path -> its Drive folder id
        self.drive_asked: set[str] = set()            # folder paths whose Drive side was asked for
        self.loading_row: dict[str, str] = {}         # folder path -> its "Loading Google Drive…" row
        self.results: queue.Queue = queue.Queue()

        # A saved pattern that names one existing file/folder shows as a ticked tree item; others
        # (like *.psd, or something only on Drive) are kept as they are until the tree shows them.
        ticked = []
        for p in patterns:
            if p in common_names:
                continue
            rel = self._existing_rel(p)
            if rel:
                self.paths.add(p)
                ticked.append(rel)
            else:
                self.other.append(p)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        self.count_lbl = ctk.CTkLabel(bottom, text="", font=font(12), text_color=MUTED)
        self.count_lbl.pack(side="left")
        ctk.CTkButton(bottom, text="Cancel", width=90, command=self.destroy, **SECONDARY).pack(side="right")
        ctk.CTkButton(bottom, text="Done", width=90, command=self.done).pack(side="right", padx=(0, 8))

        ctk.CTkLabel(self, text="Common", font=font(13, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=14)
        for i, (p, what) in enumerate(COMMON):
            ctk.CTkCheckBox(grid, text=f"{p}  ·  {what}", variable=self.common[p], font=font(12),
                            checkbox_width=18, checkbox_height=18, command=self.refresh).grid(
                row=i // 2, column=i % 2, sticky="w", padx=4, pady=2)
        grid.columnconfigure((0, 1), weight=1, uniform="c")

        where = f"{local} and {drive_label}" if drive_list and drive_label else local
        ctk.CTkLabel(self, text=f"In {where}", font=font(13, "bold"), anchor="w", justify="left",
                     wraplength=560).pack(fill="x", padx=18, pady=(14, 0))
        help_text = "Click a file or folder to skip it (and everything in it). Skipped items aren't synced " \
                    "either way and are left alone on both sides."
        if drive_list:
            help_text += f" Items marked “{ONLY_DRIVE}” are never downloaded to this PC if you skip them."
        help_text += f"  {INHERITED} = skipped by a rule above or a skipped folder."
        ctk.CTkLabel(self, text=help_text, font=font(12), text_color=MUTED, anchor="w", justify="left",
                     wraplength=560).pack(fill="x", padx=18)
        self.other_lbl = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED, anchor="w",
                                      justify="left", wraplength=560)
        self.other_lbl.pack(side="bottom", fill="x", padx=18, pady=(0, 6))

        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        box.pack(fill="both", expand=True, padx=16, pady=8)
        self._style()
        self.tree = ttk.Treeview(box, show="tree", selectmode="browse", style="Skip.Treeview")
        bar = ctk.CTkScrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", padx=(0, 4), pady=6)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        self.tree.tag_configure("inherited", foreground="#9aa0a6")
        self.tree.tag_configure("note", foreground="#9aa0a6")
        self.tree.tag_configure("drive", foreground="#1a73e8")
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", lambda _: self.toggle(self.tree.focus()))

        if os.path.isdir(local) or drive_list:
            self._fill("", "")
            for rel in ticked:
                self._reveal(rel)
        else:
            self.tree.insert("", "end", text="Choose the folder on this PC first.", tags=("note",))
        self.refresh()

        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        scale = self._get_window_scaling()
        w, h = round(620 * scale), round(640 * scale)
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = min(parent.winfo_rooty(), self.winfo_screenheight() - h - round(80 * scale))
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._grab)
        self.after(100, self._poll)
        self.wait_window()

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _style(self):
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg = ("#2b2b2b", "#e3e3e3") if dark else ("#f1f3f4", "#1f1f1f")
        style = ttk.Style(self)
        style.configure("Skip.Treeview", background=bg, fieldbackground=bg, foreground=fg, borderwidth=0,
                        font=("Segoe UI", 10), rowheight=round(24 * self._get_window_scaling()))
        style.map("Skip.Treeview", background=[("selected", "#1a73e8" if not dark else "#3c4043")],
                  foreground=[("selected", "#ffffff")])
        style.layout("Skip.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _existing_rel(self, pattern: str) -> str | None:
        """The path inside the synced folder that this pattern picks out exactly, if it was made here."""
        if not pattern or not os.path.isdir(self.local):
            return None
        try:
            hits = glob.glob(os.path.join(glob.escape(self.local), *pattern.split("/")), include_hidden=True)
        except (OSError, ValueError):
            return None
        for hit in hits[:50]:
            rel = os.path.relpath(hit, self.local).replace(os.sep, "/")
            if pattern_for(rel) == pattern:
                return rel
        return None

    def _reveal(self, rel: str):
        """Open the folders above an item ticked before, so it's in view."""
        parts = rel.split("/")
        for i in range(1, len(parts)):
            item = self.item_of.get("/".join(parts[:i]))
            if item is None:
                return
            if item not in self.loaded:
                self._fill(item, self.rel_of[item])
            self.tree.item(item, open=True)

    # -- the tree ------------------------------------------------------------------
    def _add_row(self, parent_item: str, rel: str, is_dir: bool, only_drive: bool) -> str:
        item = self.tree.insert(parent_item, "end", text=rel.rsplit("/", 1)[-1],
                                values=("dir" if is_dir else "", "drive" if only_drive else ""))
        self.rel_of[item] = rel
        self.item_of[rel] = item
        if is_dir:
            self.tree.insert(item, "end", text="…")    # placeholder so it can be opened
        return item

    def _fill(self, parent_item: str, rel_dir: str):
        """Put one folder's contents in the tree: what's on this PC now, what's on Drive when it
        arrives. Subfolders are only read when opened, so big folders stay fast."""
        self.loaded.add(parent_item)
        for child in self.tree.get_children(parent_item):
            self.tree.delete(child)
        path = os.path.join(self.local, *rel_dir.split("/")) if rel_dir else self.local
        entries = []
        try:
            with os.scandir(path) as it:
                entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
        except FileNotFoundError:
            pass                                       # only on Drive: its contents come from there
        except OSError as e:
            self.tree.insert(parent_item, "end", text=f"Couldn't read this folder: {e.strerror or e}",
                             tags=("note",))
        entries.sort(key=lambda x: (not x[1], x[0].lower()))
        for name, is_dir in entries[:MAX_ROWS]:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            self.on_pc.add(rel)
            self._add_row(parent_item, rel, is_dir, False)
        if len(entries) > MAX_ROWS:
            self.tree.insert(parent_item, "end", text=f"…and {len(entries) - MAX_ROWS:,} more "
                                                      "(type a pattern in the box to skip those)", tags=("note",))
        self._ask_drive(parent_item, rel_dir)

    def _ask_drive(self, parent_item: str, rel_dir: str):
        """Fetch this folder's Drive side in the background (once its Drive folder id is known)."""
        if not self.drive_list or rel_dir in self.drive_asked or rel_dir not in self.drive_ids:
            return
        if rel_dir and self.drive_ids[rel_dir] is None:
            return                                     # not on Drive
        self.drive_asked.add(rel_dir)
        self.loading_row[rel_dir] = self.tree.insert(parent_item, "end", text="Loading Google Drive…",
                                                     tags=("note",))
        folder_id = self.drive_ids[rel_dir]

        def work():
            try:
                out = self.drive_list(folder_id)
            except Exception as e:  # noqa: BLE001 - shown in the tree
                out = e
            self.results.put((parent_item, rel_dir, out))
        threading.Thread(target=work, daemon=True, name="skip-drive").start()

    def _poll(self):
        try:
            while True:
                self._add_drive(*self.results.get_nowait())
        except queue.Empty:
            pass
        except tk.TclError:
            return                                     # window closed
        if self.winfo_exists():
            self.after(100, self._poll)

    def _add_drive(self, parent_item: str, rel_dir: str, out):
        row = self.loading_row.pop(rel_dir, None)
        if row and self.tree.exists(row):
            self.tree.delete(row)
        if parent_item and not self.tree.exists(parent_item):
            return
        if isinstance(out, Exception):
            self.tree.insert(parent_item, "end", text=f"Couldn't read Google Drive: {out}", tags=("note",))
            return
        here = {self.rel_of[c].rsplit("/", 1)[-1].lower(): c for c in self.tree.get_children(parent_item)
                if c in self.rel_of}
        self.drive_done.add(rel_dir)
        added = 0
        for entry in sorted(out, key=lambda e: (not e["is_dir"], e["name"].lower())):
            rel = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if entry["is_dir"]:
                self.drive_ids.setdefault(rel, entry["id"])
            key = entry["name"].lower()
            if key in here:
                self.on_drive.add(self.rel_of[here[key]])   # on both sides: one row, under the PC's name
                continue
            self.on_drive.add(rel)
            if added >= MAX_ROWS:
                continue
            here[key] = self._add_row(parent_item, rel, entry["is_dir"], True)
            added += 1
        # Keep folders first, then files, by name.
        rows = [c for c in self.tree.get_children(parent_item) if c in self.rel_of]
        rows.sort(key=lambda c: (not self._vals(c)[0], self.rel_of[c].rsplit("/", 1)[-1].lower()))
        for i, c in enumerate(rows):
            self.tree.move(c, parent_item, i)
        # Saved patterns for things only on Drive now have a row: show them ticked.
        for p in list(self.other):
            if any(pattern_for(self.rel_of[c]) == p for c in rows):
                self.other.remove(p)
                self.paths.add(p)
        # Subfolders opened before their Drive ids were known.
        for c in rows:
            if c in self.loaded:
                self._ask_drive(c, self.rel_of[c])
        self.refresh()

    def _vals(self, item: str) -> tuple[str, str]:
        """(is a folder, only on Drive) of a row - each "" or a marker."""
        v = [str(x) for x in self.tree.item(item, "values") or ()] + ["", ""]
        return v[0], v[1]

    def _on_open(self, _event):
        item = self.tree.focus()
        if item in self.rel_of and item not in self.loaded:
            self._fill(item, self.rel_of[item])
            self.refresh()

    def _on_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item or self.tree.identify_element(event.x, event.y) == "Treeitem.indicator":
            return None                                   # the expand arrow: let the tree open it
        self.tree.focus(item)
        self.tree.selection_set(item)
        self.toggle(item)
        return "break"

    def toggle(self, item: str):
        rel = self.rel_of.get(item)
        if rel is None or self._inherited(rel):
            return
        p = pattern_for(rel)
        (self.paths.discard if p in self.paths else self.paths.add)(p)
        self.refresh()

    # -- state ---------------------------------------------------------------------
    def _rules(self) -> list[str]:
        return [p for p, v in self.common.items() if v.get()] + self.other + self.always

    def _matches(self, rel: str, patterns) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in patterns)

    def _inherited(self, rel: str) -> bool:
        """Skipped by a rule, or inside a skipped folder - so its own box can't be changed."""
        parts = rel.split("/")
        rules = self._rules()
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if self._matches(sub, rules) or (i < len(parts) and pattern_for(sub) in self.paths):
                return True
        return False

    def refresh(self):
        def walk(parent):
            for item in self.tree.get_children(parent):
                rel = self.rel_of.get(item)
                if rel is None:
                    continue
                name = rel.rsplit("/", 1)[-1]
                is_dir, only_drive = self._vals(item)
                icon = "📁" if is_dir else "📄"
                suffix = f"   ·  {ONLY_DRIVE}" if only_drive else ""
                if self._inherited(rel):
                    box, tags = INHERITED, ("inherited",)
                else:
                    box, tags = (ON if pattern_for(rel) in self.paths else OFF), (("drive",) if only_drive else ())
                self.tree.item(item, text=f"{box}  {icon}  {name}{suffix}", tags=tags)
                if item in self.loaded:
                    walk(item)
        walk("")
        n = len(self.result_patterns())
        self.count_lbl.configure(text=f"{n} rule{'s' if n != 1 else ''}" if n else "Nothing skipped")
        self.other_lbl.configure(text=("Also skipping (typed): " + ", ".join(self.other)) if self.other else "")

    def result_patterns(self) -> list[str]:
        common = [p for p, _ in COMMON if self.common[p].get()]
        return common + sorted(self.paths, key=str.lower) + self.other

    def where_of(self, rel: str) -> str | None:
        """Where a tree item is: "pc", "drive" or "both" - None while that's not known yet (its
        folder's Drive side hasn't loaded, or there's no Drive side to look at)."""
        pc, drive = rel in self.on_pc, rel in self.on_drive
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if pc and drive:
            return "both"
        if drive:
            return "drive"
        if pc and parent in self.drive_done:
            return "pc"
        return None

    def done(self):
        self.result = self.result_patterns()
        for rel, item in self.item_of.items():
            p = pattern_for(rel)
            if p in self.paths:
                w = self.where_of(rel)
                if w:
                    self.result_where[p] = w
        for p in self.result:                          # not shown this time: keep what was known
            if p not in self.result_where and p in self.where_before:
                self.result_where[p] = self.where_before[p]
        self.destroy()
