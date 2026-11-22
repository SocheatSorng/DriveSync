"""
Sync progress: overall percentage, the file being transferred (with its own
percentage for big files) and "file X of Y" - shown in the tray menu, the tray
tooltip and the window.

The overall percentage is weighted by size, so one big video counts for what it
really takes instead of "1 file of 30".

The sync engines call this from the sync thread; on_change is throttled so the
UI isn't flooded (a sync of thousands of unchanged files would otherwise send
thousands of updates).
"""
from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
from collections.abc import Callable


class Paused(BaseException):
    """Raised inside a sync when the user pauses (or quits): the sync stops at the next file, or part-way
    through a big transfer. A BaseException so the engines' per-file `except Exception` lets it through;
    what was finished is saved, the rest is done when syncing resumes."""


VERBS = {"upload": "Uploading", "update": "Updating", "download": "Downloading"}


def count_files(root: str, excludes: list[str]) -> tuple[int, int]:
    """(files, total bytes) a sync of `root` will look at (same exclusion rules as the sync engines)."""
    n = size = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"

        def skip(name, rel_dir=rel_dir):
            return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel_dir + name, p) for p in excludes)

        dirnames[:] = [d for d in dirnames if not skip(d)]
        for f in filenames:
            if skip(f):
                continue
            n += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
            except OSError as e:  # vanished or locked: it just doesn't add to the size estimate
                logging.getLogger("drive_sync").debug("Can't size %s: %s", f, e)
    return n, size


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Progress:
    def __init__(self, on_change: Callable[[Progress], None] | None = None, min_interval: float = 0.3,
                 clock: Callable[[], float] = time.monotonic, should_stop: Callable[[], bool] | None = None):
        self.on_change, self.min_interval, self.clock = on_change, min_interval, clock
        self.should_stop = should_stop  # True: the next transfer (or transfer piece) raises Paused
        self.reading: str | None = None  # big file being read to see whether it changed
        self.lock = threading.Lock()
        self.total = 0          # files to look at (two-way adds files found only on Drive)
        self.done = 0           # files finished (most are just "unchanged")
        self.total_bytes = 0
        self.done_bytes = 0
        self.folder = ""        # PC folder being synced
        self._folder_start = self._folder_total = 0
        self._folder_start_bytes = self._folder_bytes = 0
        self.active: dict[str, dict] = {}  # transfers in progress (several downloads run at once)
        self._last = ""         # most recent transfer started (uploads report a fraction for it)
        self._shown = 0         # shown percentage: doesn't flicker back while the total stays the same
        self._shown_for = (0, 0)  # (files, bytes) total it was worked out for
        self._last_emit = 0.0

    # -- called by the app --------------------------------------------------------
    def begin_folder(self, folder: str, files: int, size: int = 0) -> None:
        with self.lock:
            self.folder, self._folder_start, self._folder_total = folder, self.done, files
            self._folder_start_bytes, self._folder_bytes = self.done_bytes, size
        self._changed(force=True)

    def end_folder(self) -> None:
        with self.lock:  # files moved to the Recycle Bin as a whole folder etc. still count as done
            self.done = max(self.done, self._folder_start + self._folder_total)
            self.done_bytes = max(self.done_bytes, self._folder_start_bytes + self._folder_bytes)
            self.active.clear()
        self._changed(force=True)

    # -- called by the sync engines (any thread) ------------------------------------------
    def add_total(self, n: int = 1, size: int = 0) -> None:
        with self.lock:
            self.total += n
            self.total_bytes += size

    def file_done(self, size: int = 0) -> None:
        with self.lock:
            self.done += 1
            self.done_bytes += size
        self._changed()

    def check(self) -> None:
        """Raise Paused if syncing should stop now."""
        if self.should_stop and self.should_stop():
            raise Paused()

    def read_start(self, path: str) -> None:
        """A big file is being read (checksum) - shown instead of a bare "Checking files"."""
        self.reading = path
        self._changed(force=True)

    def read_end(self) -> None:
        self.reading = None

    def transfer(self, action: str, path: str, size: int = 0) -> None:
        self.check()
        with self.lock:
            self.active[path] = {"action": action, "size": size, "got": 0, "started": self.clock()}
            self._last = path
        self._changed(force=True)

    def transfer_fraction(self, fraction: float) -> None:
        """Progress of the most recent transfer as 0..1 (how uploads report)."""
        self.check()
        with self.lock:
            t = self.active.get(self._last)
            if t:
                t["got"] = max(0.0, min(1.0, fraction)) * t["size"]
        self._changed()

    def transfer_bytes(self, path: str, got: int) -> None:
        """Bytes received so far for one transfer (how parallel downloads report)."""
        self.check()
        with self.lock:
            t = self.active.get(path)
            if t:
                t["got"] = got
        self._changed()

    def transfer_end(self, path: str | None = None) -> None:
        with self.lock:
            self.active.pop(path if path is not None else self._last, None)

    # -- reading ------------------------------------------------------------------------
    def _primary(self) -> dict | None:
        """The transfer to describe: the most recent one still running."""
        if not self.active:
            return None
        key = self._last if self._last in self.active else next(reversed(self.active))
        return {"path": key, **self.active[key]}

    @property
    def action(self) -> str | None:
        with self.lock:
            p = self._primary()
        return p["action"] if p else None

    @property
    def fraction(self) -> float:
        with self.lock:
            p = self._primary()
        return p["got"] / p["size"] if p and p["size"] else 0.0

    @property
    def percent(self) -> int:
        with self.lock:
            moving = sum(t["got"] for t in self.active.values())
            if self.total_bytes > 0:   # by size: a file counts for how much there is to move
                raw = int(100 * (self.done_bytes + moving) / self.total_bytes)
            elif self.total > 0:       # only empty files: by count
                part = sum(t["got"] / t["size"] for t in self.active.values() if t["size"])
                raw = int(100 * (self.done + part) / self.total)
            else:
                return 0
            raw = min(99, raw)       # 100 only once the sync has finished
            totals = (self.total, self.total_bytes)
            if totals != self._shown_for:
                # More to do was found (a quick sync counts each folder as it gets there, two-way adds
                # files found only on Drive): show the true figure, even if it's lower than before.
                # Holding the old, higher number is how "99%" stuck at file 855 of 7,483.
                self._shown, self._shown_for = raw, totals
            else:
                self._shown = max(self._shown, raw)   # same total: never jitter backwards
            return self._shown

    def speed(self) -> float | None:
        """Combined bytes per second of the running transfers (None until there's enough to tell)."""
        now = self.clock()
        with self.lock:
            rates = [t["got"] / (now - t["started"]) for t in self.active.values()
                     if now - t["started"] >= 2 and t["got"] > 0]
        return sum(rates) if rates else None

    def current(self, max_len: int = 60) -> str:
        """'Uploading Term 1/quiz.pdf (45% of 12.0 MB · 1.2 MB/s · about 2 min left)',
        'Downloading 4 files at once (2.1 MB/s) · quiz.pdf, plan.docx, …' or 'Checking files'."""
        with self.lock:
            active = [{"path": k, **v} for k, v in self.active.items()]
            primary = self._primary()
        def short(path, n=max_len):
            return path if len(path) <= n else "…" + path[-(n - 1):]

        if not active:
            reading = self.reading
            return f"Checking {short(reading, 100)}" if reading else "Checking files"

        if len(active) > 1:   # the files themselves are listed by file_lines()
            rate = self.speed()
            verb = VERBS.get(active[0]["action"], active[0]["action"])
            return f"{verb} {len(active)} files at once" + (f" ({human_size(rate)}/s)" if rate else "")
        t = primary
        size, frac = t["size"], (t["got"] / t["size"] if t["size"] else 0.0)
        extra = ""
        if size >= 1024 * 1024:   # big files report progress while they transfer
            bits = [f"{int(frac * 100)}% of {human_size(size)}"]
            rate = self.speed()
            if rate:
                bits.append(f"{human_size(rate)}/s")
                left = (1 - frac) * size / rate
                bits.append("less than a minute left" if left < 60 else
                            f"about {round(left / 60)} min left" if left < 3600 else
                            f"about {left / 3600:.1f} h left")
            extra = f" ({' · '.join(bits)})"
        elif size:
            extra = f" ({human_size(size)})"
        return f"{VERBS.get(t['action'], t['action'])} {short(t['path'])}{extra}"

    def file_lines(self, full_path: bool = True, max_len: int = 70) -> list[str]:
        """One line per file being transferred right now (only when there's more than one),
        e.g. '↓ Application/OllamaSetup.exe - 45% of 1.5 GB'. full_path=False: name only."""
        now = self.clock()
        with self.lock:
            active = [{"path": k, **v} for k, v in self.active.items()]
        if len(active) < 2:
            return []
        lines = []
        for t in active:
            name = t["path"] if full_path else t["path"].rsplit("/", 1)[-1]
            if len(name) > max_len:
                name = "…" + name[-(max_len - 1):]
            arrow = "↓" if t["action"] == "download" else "↑"
            if t["size"]:
                pct = int(100 * t["got"] / t["size"])
                info = f"{pct}% of {human_size(t['size'])}" if t["size"] >= 1024 * 1024 else human_size(t["size"])
            else:
                info = "starting"
            elapsed = now - t["started"]
            if t["size"] >= 1024 * 1024 and elapsed >= 2 and t["got"]:
                info += f" · {human_size(t['got'] / elapsed)}/s"
            lines.append(f"{arrow} {name} - {info}")
        return lines

    def counter(self) -> str:
        with self.lock:
            return f"file {min(self.done + 1, max(self.total, 1)):,} of {max(self.total, 1):,}"

    def _changed(self, force: bool = False) -> None:
        if not self.on_change:
            return
        now = self.clock()
        if not force and now - self._last_emit < self.min_interval:
            return
        self._last_emit = now
        try:
            self.on_change(self)
        except Exception as e:  # noqa: BLE001 - showing progress must never break a sync
            logging.getLogger("drive_sync").debug("Progress display failed: %s", e)
