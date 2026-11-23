"""
Activity history for the Activity tab.

Instead of raw log lines, Drive Sync records:
  * one "run" per folder sync that actually did something (uploads, updates, trash,
    new folders, problems) - with the list of files involved;
  * a few important "events" (signed in, account removed, can't reach Google, ...).

Runs that changed nothing are not stored (they'd bury the useful entries); the tab
shows "Last checked" instead. History is saved as JSON lines in activity.jsonl and
trimmed to the newest KEEP entries.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

KEEP = 1000            # entries kept on disk
MAX_ITEMS = 500        # files listed per run (the rest are only counted)
KINDS = ("uploaded", "updated", "moved", "downloaded", "deleted_local", "trashed", "folder", "conflict", "skipped",
         "error")
CHANGE_KINDS = ("uploaded", "updated", "moved", "downloaded", "deleted_local", "trashed", "folder", "conflict")
TRIGGERS = {"startup": "when Drive Sync started", "scheduled": "scheduled check",
            "changes": "after files changed", "drive": "after changes on Google Drive", "manual": "Sync now"}

log = logging.getLogger("drive_sync")


# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #
def new_run(folder: str, account: str | None, trigger: str, drive_folder: str = "") -> dict:
    return {"type": "run", "ts": time.time(), "folder": folder, "account": account or "",
            "drive_folder": drive_folder, "trigger": trigger,
            "counts": {k: 0 for k in KINDS}, "items": [], "more": 0, "duration": 0.0}


def add_item(run: dict, kind: str, path: str, message: str | None = None) -> None:
    run["counts"][kind] = run["counts"].get(kind, 0) + 1
    if len(run["items"]) < MAX_ITEMS or kind == "error":  # never drop a problem
        item = {"kind": kind, "path": path, "t": round(time.time())}  # when it finished (older entries have none)
        if message:
            item["message"] = message
        run["items"].append(item)
    else:
        run["more"] += 1


def new_event(level: str, message: str, detail: str = "") -> dict:
    """level: info | warning | error"""
    return {"type": "event", "ts": time.time(), "level": level, "message": message, "detail": detail}


def has_problems(entry: dict) -> bool:
    if entry["type"] == "event":
        return entry.get("level") in ("warning", "error")
    c = entry["counts"]
    return c.get("error", 0) > 0 or c.get("skipped", 0) > 0 or c.get("conflict", 0) > 0


def has_changes(entry: dict) -> bool:
    return entry["type"] == "run" and any(entry["counts"].get(k, 0) for k in CHANGE_KINDS)


def is_worth_keeping(entry: dict) -> bool:
    return entry["type"] == "event" or has_changes(entry) or has_problems(entry)


def summary(entry: dict) -> str:
    """'3 uploaded · 1 updated · 1 problem'"""
    if entry["type"] == "event":
        return entry.get("detail", "")
    c = entry["counts"]
    labels = [("uploaded", "uploaded"), ("updated", "updated"), ("moved", "moved on Drive"),
              ("downloaded", "downloaded"),
              ("deleted_local", "moved to Recycle Bin"), ("trashed", "moved to Drive trash"),
              ("folder", "new folder"), ("conflict", "conflict"), ("skipped", "skipped"), ("error", "problem")]
    parts = []
    for key, label in labels:
        n = c.get(key, 0)
        if n:
            plural = "s" if n != 1 and key in ("folder", "error", "conflict") else ""
            parts.append(f"{n} {label}{plural}")
    return " · ".join(parts) or "No changes"


def matches(entry: dict, view: str, query: str) -> bool:
    """view: All | Changes | Problems; query: case-insensitive text in folder/file names/messages."""
    if view == "Changes" and not has_changes(entry):
        return False
    if view == "Problems" and not has_problems(entry):
        return False
    q = query.strip().lower()
    if not q:
        return True
    if entry["type"] == "event":
        hay = [entry.get("message", ""), entry.get("detail", "")]
    else:
        hay = [entry.get("folder", ""), entry.get("account", ""), entry.get("drive_folder", "")]
        hay += [i["path"] for i in entry["items"]] + [i.get("message", "") for i in entry["items"]]
    return any(q in h.lower() for h in hay)


def time_range(entry: dict) -> str:
    """'22:52' for a quick run, '22:52 – 01:37' for one that ran into a later minute."""
    start = time.strftime("%H:%M", time.localtime(entry["ts"]))
    duration = entry.get("duration") or 0
    end = time.strftime("%H:%M", time.localtime(entry["ts"] + duration))
    return start if end == start else f"{start} – {end}"


def item_times(items: list[dict], run_ts: float) -> list[str]:
    """A time for each file row, '' when it's the same as the row above (so the list reads like a timeline).
    Files finished on a later day than the run started show the weekday too ('Mon 00:12')."""
    start_day = datetime.fromtimestamp(run_ts).date()
    out, prev = [], None
    for it in items:
        t = it.get("t")
        if t is None:
            label = ""
        else:
            when = datetime.fromtimestamp(t)
            label = when.strftime("%H:%M" if when.date() == start_day else "%a %H:%M")
        out.append("" if label == prev else label)
        prev = label
    return out


def day_label(ts: float, now: float | None = None) -> str:
    d = datetime.fromtimestamp(ts).date()
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    if d.year == today.year:
        return d.strftime("%A, %d %B")
    return d.strftime("%d %B %Y")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class ActivityStore:
    def __init__(self, path: Path, keep: int = KEEP):
        self.path, self.keep = path, keep
        self.lock = threading.Lock()
        self._lines = None  # lines in the file, counted lazily

    def load(self) -> list[dict]:
        """Saved entries, oldest first."""
        with self.lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            self._lines = len(lines)
            out = []
            for line in lines[-self.keep:]:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a half-written line from a crash
            return out

    def append(self, entry: dict) -> None:
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if self._lines is None:
                    with open(self.path, encoding="utf-8") as f:
                        self._lines = sum(1 for _ in f)
                else:
                    self._lines += 1
                if self._lines > self.keep * 2:  # trim occasionally, not on every write
                    lines = self.path.read_text(encoding="utf-8").splitlines()[-self.keep:]
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    tmp.replace(self.path)
                    self._lines = len(lines)
            except OSError as e:
                log.warning("Couldn't save activity history: %s", e)

    def clear(self) -> None:
        with self.lock:
            self.path.unlink(missing_ok=True)
            self._lines = 0
