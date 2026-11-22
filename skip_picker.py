"""Choose what a synced folder skips: tick common items, or files and folders in a tree of the PC folder.

The result is the same list of patterns the "Skip files or folders matching" box holds: a name like
`node_modules` (skipped anywhere) or a path inside the synced folder like `Project/.venv` (just that one).
"""
from __future__ import annotations

import fnmatch
import glob
import os
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

MUTED = ("#5f6368", "#9aa0a6")
CARD = ("#f1f3f4", "#2b2b2b")
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}
MAX_ROWS = 1000          # entries shown per folder in the tree (the rest are summed up in one row)
OFF, ON, INHERITED = "☐", "☑", "▣"

# (pattern, what it is) - things people commonly don't want synced.
COMMON = [
    (".git", "Git repositories"),
    ("node_modules", "Node.js packages"),
    (".venv", "Python virtual environments"),
    ("venv", "Python virtual environments"),
    ("__pycache__", "Python cache"),
    (".vs", "Visual Studio files"),
    ("*.log", "Log files"),
    ("*.bak", "Backup copies"),
    ("*.iso", "Disc images"),
]


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def pattern_for(rel: str) -> str:
    """The pattern that skips exactly this path (relative to the synced folder, "/"-separated).
    Brackets, * and ? in names are escaped so they match literally; a comma becomes ? because the
    box separates patterns with commas."""
    return glob.escape(rel).replace(",", "?")


def split_patterns(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


class SkipPicker(ctk.CTkToplevel):
    """self.result = the chosen list of patterns, or None if cancelled."""

    def __init__(self, parent, local: str, patterns: list[str], always: list[str] = ()):
        super().__init__(parent)
        self.title("Choose what to skip")
        self.geometry("620x620")
        self.minsize(480, 460)
        self.transient(parent)
        self.result = None
        self.local = local
        self.always = list(always)                    # built-in junk patterns: shown as skipped, not returned
        common_names = {p for p, _ in COMMON}
        self.common = {p: tk.BooleanVar(value=p in patterns) for p in common_names}
        self.paths: set[str] = set()                  # patterns for ticked tree items
        self.other: list[str] = []                    # typed patterns the picker has no tick box for
        self.rel_of: dict[str, str] = {}              # tree item id -> path inside the synced folder
        self.item_of: dict[str, str] = {}             # and back
        self.loaded: set[str] = set()                 # folders whose contents are in the tree

        # A saved pattern that names one existing file/folder shows as a ticked tree item; others
        # (like *.psd) are kept as they are.
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

        ctk.CTkLabel(self, text=f"In {local}", font=font(13, "bold"), anchor="w").pack(
            fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(self, text="Click a file or folder to skip it (and everything in it). "
                                f"{INHERITED} = skipped because of a rule above or a skipped folder.",
                     font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=560).pack(
            fill="x", padx=18)
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
        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", lambda _: self.toggle(self.tree.focus()))

        if os.path.isdir(local):
            self._fill("", "")
            for rel in ticked:
                self._reveal(rel)
        else:
            self.tree.insert("", "end", text="Choose the folder on this PC first.", tags=("note",))
        self.refresh()

        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        scale = self._get_window_scaling()
        w, h = round(620 * scale), round(620 * scale)
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = min(parent.winfo_rooty(), self.winfo_screenheight() - h - round(80 * scale))
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._grab)
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
    def _fill(self, parent_item: str, rel_dir: str):
        """Put one folder's contents in the tree: folders first, then files (biggest folders stay fast:
        subfolders are only read when opened)."""
        self.loaded.add(parent_item)
        for child in self.tree.get_children(parent_item):
            self.tree.delete(child)
        path = os.path.join(self.local, *rel_dir.split("/")) if rel_dir else self.local
        try:
            with os.scandir(path) as it:
                entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
        except OSError as e:
            self.tree.insert(parent_item, "end", text=f"Couldn't read this folder: {e.strerror or e}",
                             tags=("note",))
            return
        entries.sort(key=lambda x: (not x[1], x[0].lower()))
        for name, is_dir in entries[:MAX_ROWS]:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            item = self.tree.insert(parent_item, "end", text=name)
            self.rel_of[item] = rel
            self.item_of[rel] = item
            if is_dir:
                self.tree.item(item, values=("dir",))
                self.tree.insert(item, "end", text="…")    # placeholder so it can be opened
        if len(entries) > MAX_ROWS:
            self.tree.insert(parent_item, "end", text=f"…and {len(entries) - MAX_ROWS:,} more "
                                                      "(type a pattern in the box to skip those)", tags=("note",))

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
                icon = "📁" if self.tree.item(item, "values") else "📄"
                if self._inherited(rel):
                    box, tags = INHERITED, ("inherited",)
                else:
                    box, tags = (ON if pattern_for(rel) in self.paths else OFF), ()
                self.tree.item(item, text=f"{box}  {icon}  {name}", tags=tags)
                if item in self.loaded:
                    walk(item)
        walk("")
        n = len(self.result_patterns())
        self.count_lbl.configure(text=f"{n} rule{'s' if n != 1 else ''}" if n else "Nothing skipped")
        self.other_lbl.configure(text=("Also skipping (typed): " + ", ".join(self.other)) if self.other else "")

    def result_patterns(self) -> list[str]:
        common = [p for p, _ in COMMON if self.common[p].get()]
        return common + sorted(self.paths, key=str.lower) + self.other

    def done(self):
        self.result = self.result_patterns()
        self.destroy()
