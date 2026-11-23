"""Choosing what a synced folder skips, without the window: the patterns, the tick boxes' state, the
PC and Google Drive listings merged into one tree, and which side each ticked item is on.

Used by the Qt skip picker (qtui/skip_picker.py); the helpers at the top are also used by the Tk picker
(skip_picker.py) and ui_model. A pattern is a name like `node_modules` (skipped anywhere) or a path
inside the synced folder like `Project/.venv` (just that one). A skipped item isn't synced either way.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import threading

MAX_ROWS = 1000          # entries shown per folder in the tree (the rest are summed up in one row)
OFF, ON, INHERITED = "☐", "☑", "▣"
ONLY_DRIVE = "only on Google Drive"

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


def pattern_for(rel: str) -> str:
    """The pattern that skips exactly this path (relative to the synced folder, "/"-separated).
    Brackets, * and ? in names are escaped so they match literally; a comma becomes ? because the
    box separates patterns with commas."""
    return glob.escape(rel).replace(",", "?")


def split_patterns(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def is_rule(pattern: str) -> bool:
    """A wildcard rule like *.log (rather than one picked file/folder)?"""
    return any(c in "*?[" for c in re.sub(r"\[[*?\[]\]", "", pattern))


def describe(pattern: str) -> tuple[str, str]:
    """(short, full) text to show for a skip pattern: escapes undone, a path shortened to its name."""
    if is_rule(pattern):
        return pattern, pattern
    full = re.sub(r"\[([*?\[])\]", r"\1", pattern)
    return full.rsplit("/", 1)[-1], full


def drive_lister(app, email: str, root_id: str | None, path: str):
    """list(folder_id) for the picker: what's in a folder of this synced folder's Drive side, as
    [{"name", "id", "is_dir"}]. folder_id None = the synced Drive folder itself ([] if it doesn't
    exist yet). Runs on the picker's background thread; one Drive connection, one call at a time."""
    from drive_sync import FOLDER_MIME
    lock, conn, root = threading.Lock(), {}, {"id": root_id}

    def find_root(d):
        parent = "root"
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if parts and parts[0].lower() == "my drive":
            parts = parts[1:]
        for part in parts:
            parent = d.find_folder(part, parent)
            if parent is None:
                return None
        return parent

    def list_folder(folder_id):
        with lock:
            if "drive" not in conn:
                conn["drive"] = app.drive_for_browsing(email)
            d = conn["drive"]
            if folder_id is None:
                if root["id"] is None:
                    root["id"] = find_root(d)
                folder_id = root["id"]
                if folder_id is None:
                    return []
            # Google Docs/Sheets, shortcuts etc. are never synced, so they aren't offered.
            return [{"name": i["name"], "id": i["id"], "is_dir": i["mimeType"] == FOLDER_MIME}
                    for i in d.list_children(folder_id) if i["mimeType"] == FOLDER_MIME or "md5Checksum" in i]
    return list_folder


def drive_side(app, email: str | None, local: str, drive_text: str, drive_id: str | None,
               chosen_label: str | None):
    """(lister, label) for the folder's Google Drive side, or (None, "") without an account."""
    from pathlib import Path

    import ui_model
    if not email:
        return None, ""
    if drive_id and drive_text == chosen_label:
        root_id, path, label = drive_id, "", chosen_label
    else:
        path = ui_model.strip_my_drive(drive_text) or f"PC Backup/{Path(local).name}"
        root_id, label = None, f"My Drive/{path}"
    return drive_lister(app, email, root_id, path), label


class SkipModel:
    """The picker's state. Paths ("rel") are relative to the synced folder, "/"-separated."""

    def __init__(self, local: str, patterns: list[str], always: list[str] = (), has_drive: bool = False,
                 where: dict | None = None):
        self.local = local
        self.always = list(always)                 # built-in junk patterns: shown as skipped, not returned
        self.has_drive = has_drive
        self.where_before = dict(where or {})
        names = {p for p, _ in COMMON}
        self.common: dict[str, bool] = {p: p in patterns for p in names}
        self.paths: set[str] = set()               # patterns for ticked tree items
        self.other: list[str] = []                 # typed patterns the picker has no tick box for
        self.ticked_rels: list[str] = []           # items ticked before, to show opened in the tree
        self.on_pc: set[str] = set()               # paths seen on this PC
        self.on_drive: set[str] = set()            # paths seen on Google Drive
        self.drive_done: set[str] = set()          # folders whose Drive side has loaded
        self.drive_ids: dict[str, str | None] = {"": None}   # folder path -> its Drive folder id
        self.drive_asked: set[str] = set()         # folder paths whose Drive side was asked for
        # A saved pattern that names one existing file/folder shows as a ticked tree item; others
        # (like *.psd, or something only on Drive) are kept as they are until the tree shows them.
        for p in patterns:
            if p in names:
                continue
            rel = self.existing_rel(p)
            if rel:
                self.paths.add(p)
                self.ticked_rels.append(rel)
            else:
                self.other.append(p)

    def can_show(self) -> bool:
        return os.path.isdir(self.local) or self.has_drive

    def existing_rel(self, pattern: str) -> str | None:
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

    # ------------------------------------------------------------- listings
    def list_pc(self, rel_dir: str) -> tuple[list[tuple[str, bool]], int, str | None]:
        """(entries shown as (name, is folder) - folders first, how many more aren't shown, error text)."""
        path = os.path.join(self.local, *rel_dir.split("/")) if rel_dir else self.local
        entries, error = [], None
        try:
            with os.scandir(path) as it:
                entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
        except FileNotFoundError:
            pass                                   # only on Drive: its contents come from there
        except OSError as e:
            error = f"Couldn't read this folder: {e.strerror or e}"
        entries.sort(key=lambda x: (not x[1], x[0].lower()))
        shown = entries[:MAX_ROWS]
        for name, _is_dir in shown:
            self.on_pc.add(f"{rel_dir}/{name}" if rel_dir else name)
        return shown, len(entries) - len(shown), error

    def drive_to_ask(self, rel_dir: str) -> tuple[bool, str | None]:
        """(ask, folder id): whether this folder's Drive side should be fetched now (once)."""
        if not self.has_drive or rel_dir in self.drive_asked or rel_dir not in self.drive_ids:
            return False, None
        if rel_dir and self.drive_ids[rel_dir] is None:
            return False, None                      # not on Drive
        self.drive_asked.add(rel_dir)
        return True, self.drive_ids[rel_dir]

    def merge_drive(self, rel_dir: str, here: dict[str, str], out: list[dict]) -> list[tuple[str, bool]]:
        """Drive's listing of a folder arrived. here: {lower-case name: rel} of the rows already shown.
        Returns the Drive-only rows to add as (rel, is folder)."""
        self.drive_done.add(rel_dir)
        added = []
        for entry in sorted(out, key=lambda e: (not e["is_dir"], e["name"].lower())):
            rel = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if entry["is_dir"]:
                self.drive_ids.setdefault(rel, entry["id"])
            key = entry["name"].lower()
            if key in here:
                self.on_drive.add(here[key])        # on both sides: one row, under the PC's name
                continue
            self.on_drive.add(rel)
            if len(added) < MAX_ROWS:
                here[key] = rel
                added.append((rel, entry["is_dir"]))
        return added

    def adopt(self, rels) -> None:
        """Saved patterns for things that now have a row (e.g. only on Drive): show them ticked."""
        shown = {pattern_for(r) for r in rels}
        for p in list(self.other):
            if p in shown:
                self.other.remove(p)
                self.paths.add(p)

    # ------------------------------------------------------------- ticking
    def rules(self) -> list[str]:
        return [p for p, on in self.common.items() if on] + self.other + self.always

    @staticmethod
    def matches(rel: str, patterns) -> bool:
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in patterns)

    def inherited(self, rel: str) -> bool:
        """Skipped by a rule, or inside a skipped folder - so its own box can't be changed."""
        parts = rel.split("/")
        rules = self.rules()
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if self.matches(sub, rules) or (i < len(parts) and pattern_for(sub) in self.paths):
                return True
        return False

    def toggle(self, rel: str) -> bool:
        """Tick / untick a tree item; False if it can't change (skipped by a rule or its folder)."""
        if self.inherited(rel):
            return False
        p = pattern_for(rel)
        (self.paths.discard if p in self.paths else self.paths.add)(p)
        return True

    def row_text(self, rel: str, is_dir: bool, only_drive: bool) -> tuple[str, str | None]:
        """(text, kind) of a tree row; kind "inherited" (greyed), "drive" (only on Drive) or None."""
        name = rel.rsplit("/", 1)[-1]
        icon = "📁" if is_dir else "📄"
        suffix = f"   ·  {ONLY_DRIVE}" if only_drive else ""
        if self.inherited(rel):
            return f"{INHERITED}  {icon}  {name}{suffix}", "inherited"
        box = ON if pattern_for(rel) in self.paths else OFF
        return f"{box}  {icon}  {name}{suffix}", "drive" if only_drive else None

    # ------------------------------------------------------------- result
    def result_patterns(self) -> list[str]:
        common = [p for p, _ in COMMON if self.common[p]]
        return common + sorted(self.paths, key=str.lower) + self.other

    def count_text(self) -> str:
        n = len(self.result_patterns())
        return f"{n} rule{'s' if n != 1 else ''}" if n else "Nothing skipped"

    def other_text(self) -> str:
        return ("Also skipping (typed): " + ", ".join(self.other)) if self.other else ""

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

    def finish(self, shown_rels) -> tuple[list[str], dict[str, str]]:
        """(patterns, which side each ticked item is on) when Done is pressed."""
        patterns = self.result_patterns()
        where: dict[str, str] = {}
        for rel in shown_rels:
            p = pattern_for(rel)
            if p in self.paths:
                w = self.where_of(rel)
                if w:
                    where[p] = w
        for p in patterns:                         # not shown this time: keep what was known
            if p not in where and p in self.where_before:
                where[p] = self.where_before[p]
        return patterns, where
