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
                 clock: Callable[[], float] = time.monotonic):
        self.on_change, self.min_interval, self.clock = on_change, min_interval, clock
        self.lock = threading.Lock()
        self.total = 0          # files to look at (two-way adds files found only on Drive)
        self.done = 0           # files finished (most are just "unchanged")
        self.total_bytes = 0
        self.done_bytes = 0
        self.folder = ""        # PC folder being synced
        self._folder_start = self._folder_total = 0
        self._folder_start_bytes = self._folder_bytes = 0
        self.action = None      # "upload" | "update" | "download" | None
        self.path = ""          # file being transferred
        self.size = 0
        self.fraction = 0.0     # of the current transfer (big files report as they go)
        self._started = 0.0     # when the current transfer started (for speed / time left)
        self._shown = 0         # never let the shown percentage go backwards
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
            self.action = None
        self._changed(force=True)

    # -- called by the sync engines ---------------------------------------------------
    def add_total(self, n: int = 1, size: int = 0) -> None:
        with self.lock:
            self.total += n
            self.total_bytes += size

    def file_done(self, size: int = 0) -> None:
        with self.lock:
            self.done += 1
            self.done_bytes += size
        self._changed()

    def transfer(self, action: str, path: str, size: int = 0) -> None:
        with self.lock:
            self.action, self.path, self.size, self.fraction = action, path, size, 0.0
            self._started = self.clock()
        self._changed(force=True)

    def transfer_fraction(self, fraction: float) -> None:
        with self.lock:
            self.fraction = max(0.0, min(1.0, fraction))
        self._changed()

    def transfer_end(self) -> None:
        with self.lock:
            self.action, self.fraction = None, 0.0

    # -- reading ------------------------------------------------------------------------
    @property
    def percent(self) -> int:
        with self.lock:
            if self.total_bytes > 0:   # by size: a file counts for how much there is to move
                moving = self.fraction * self.size if self.action else 0
                raw = int(100 * (self.done_bytes + moving) / self.total_bytes)
            elif self.total > 0:       # only empty files: by count
                raw = int(100 * (self.done + (self.fraction if self.action else 0)) / self.total)
            else:
                return 0
            self._shown = max(self._shown, min(99, raw))  # 100 only once the sync has finished
            return self._shown

    def speed(self) -> float | None:
        """Bytes per second of the current transfer (None until there's enough to tell)."""
        with self.lock:
            if not self.action:
                return None
            moved, elapsed = self.fraction * self.size, self.clock() - self._started
        return moved / elapsed if elapsed >= 2 and moved > 0 else None

    def current(self, max_len: int = 60) -> str:
        """'Uploading Term 1/quiz.pdf (45% of 12.0 MB · 1.2 MB/s · about 2 min left)' or 'Checking files'."""
        with self.lock:
            action, path, size, frac = self.action, self.path, self.size, self.fraction
        if not action:
            return "Checking files"
        name = path if len(path) <= max_len else "…" + path[-(max_len - 1):]
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
        return f"{VERBS.get(action, action)} {name}{extra}"

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
