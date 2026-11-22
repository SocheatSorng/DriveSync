"""
Instant sync: watch synced folders for changes.

Windows reports every create / change / rename / delete inside a watched folder
(ReadDirectoryChangesW, via the watchdog library - no polling). Each change marks
that folder "dirty"; once a folder has been quiet for SETTLE_SECONDS (so a file that
is still being copied or saved isn't uploaded half-way), the app syncs just that folder.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
from collections.abc import Callable, Hashable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

SETTLE_SECONDS = 4.0
IGNORED_EVENT_TYPES = {"opened", "closed_no_write"}  # reading a file isn't a change

log = logging.getLogger("drive_sync")


def is_excluded(path: str, root: str, excludes: list[str]) -> bool:
    """Same rules as the syncer: match the name or the path relative to the folder."""
    name = os.path.basename(path)
    rel = os.path.relpath(path, root).replace(os.sep, "/") if path.startswith(root) else name
    parts = rel.split("/")
    # A change inside an excluded folder (e.g. node_modules/...) is excluded too.
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) or any(fnmatch.fnmatch(x, p) for x in parts)
               for p in excludes)


class _Handler(FileSystemEventHandler):
    def __init__(self, key: Hashable, root: str, excludes: list[str],
                 on_change: Callable[[Hashable, list[str]], None],
                 ignore_under: tuple[str, ...] = ()):
        self.key, self.root, self.excludes, self.on_change = key, os.path.normpath(root), excludes, on_change
        self.ignore_under = tuple(os.path.normcase(os.path.normpath(p)) + os.sep for p in ignore_under)
        # Last seen (modified time, size) per file. Windows also reports attribute and
        # last-access-time updates as "modified" - e.g. when Drive Sync itself reads a file to
        # check it - which must not trigger another sync.
        self.seen: dict[str, tuple[int, int] | None] = {}

    def _content_changed(self, path: str) -> bool:
        try:
            st = os.stat(path)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None  # gone already / locked: let the sync figure it out
        if len(self.seen) > 50_000:
            self.seen.clear()
        unchanged = sig is not None and self.seen.get(path) == sig
        self.seen[path] = sig
        return not unchanged

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in IGNORED_EVENT_TYPES:
            return
        if event.is_directory and event.event_type == "modified":
            return  # a folder "changes" whenever its contents do; the file events are enough
        paths = [os.path.normpath(os.fsdecode(p)) for p in (event.src_path, getattr(event, "dest_path", "")) if p]
        relevant = [p for p in paths
                    if not os.path.normcase(p).startswith(self.ignore_under)  # Drive Sync's own logs/state
                    and not is_excluded(p, self.root, self.excludes)]
        if not relevant:
            return
        if event.is_directory and event.event_type == "attrib":
            return
        if event.event_type in ("created", "modified", "attrib") and not event.is_directory:
            # Record the file's state; for "modified"/"attrib", only real content/size changes count.
            changed = [self._content_changed(p) for p in relevant]
            if event.event_type != "created" and not any(changed):
                return
        self.on_change(self.key, relevant)


class FolderWatcher:
    """Watches several folders; calls on_change(key, changed_paths) from a background thread."""

    def __init__(self, on_change: Callable[[Hashable, list[str]], None], ignore_under: tuple[str, ...] = ()):
        self.on_change = on_change
        self.ignore_under = ignore_under   # never react to changes here (the app's own files)
        self.observer = Observer()
        self.observer.daemon = True
        self.watches: dict[Hashable, tuple[str, tuple[str, ...], object]] = {}
        self.lock = threading.Lock()
        self.started = False

    def update(self, folders: dict[Hashable, tuple[str, list[str]]]) -> None:
        """Make the watched set match folders = {key: (local path, excludes)}."""
        with self.lock:
            if not self.started:
                self.observer.start()
                self.started = True
            for key in list(self.watches):
                path, excl, watch = self.watches[key]
                want = folders.get(key)
                if want is None or want[0] != path or tuple(want[1]) != excl:
                    self.observer.unschedule(watch)
                    del self.watches[key]
            for key, (path, excludes) in folders.items():
                if key in self.watches:
                    continue
                if not os.path.isdir(path):
                    log.warning("Can't watch %s (folder not found) - it will sync on the timer instead", path)
                    continue
                try:
                    handler = _Handler(key, path, list(excludes), self.on_change, self.ignore_under)
                    watch = self.observer.schedule(handler, path, recursive=True)
                except OSError as e:  # e.g. some network drives don't support change notifications
                    log.warning("Can't watch %s (%s) - it will sync on the timer instead", path, e)
                    continue
                self.watches[key] = (path, tuple(excludes), watch)

    @property
    def watched(self) -> int:
        return len(self.watches)

    def stop(self) -> None:
        with self.lock:
            if self.started:
                self.observer.stop()
                self.started = False
            self.watches.clear()


class ChangeTracker:
    """Remembers which folders changed and when; hands them out once they've settled."""

    MAX_PATHS = 5000  # beyond this, remembering paths isn't worth it: check the folder's whole tree

    def __init__(self, settle: float = SETTLE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.settle, self.clock = settle, clock
        self.due: dict[Hashable, float] = {}  # folder -> clock time when it may be synced
        self.paths: dict[Hashable, set[str] | None] = {}  # folder -> changed paths (None = too many)
        self.lock = threading.Lock()

    def mark(self, key: Hashable, settle: float | None = None, paths=()) -> None:
        """A change happened (to `paths`, if known). settle: quiet time needed (default: SETTLE_SECONDS
        for files on this PC; Drive-side changes use a shorter one). Never shortens a wait that's
        already longer, so a file still being saved on this PC is not synced early."""
        due = self.clock() + (self.settle if settle is None else settle)
        with self.lock:
            self.due[key] = max(due, self.due.get(key, due))
            if paths:
                got = self.paths.setdefault(key, set())
                if got is not None:
                    got.update(paths)
                    if len(got) > self.MAX_PATHS:
                        self.paths[key] = None

    def take_paths(self, key: Hashable) -> set[str] | None:
        """Paths changed in this folder since the last call; None = too many to list (check it all)."""
        with self.lock:
            return self.paths.pop(key, set())

    def take_settled(self) -> set[Hashable]:
        """Folders whose wait is over (removed from the pending set)."""
        now = self.clock()
        with self.lock:
            ready = {k for k, t in self.due.items() if now >= t}
            for k in ready:
                del self.due[k]
            return ready

    def next_due(self) -> float | None:
        """Clock time when the next pending folder may be synced, or None."""
        with self.lock:
            return min(self.due.values()) if self.due else None

    def clear(self) -> None:
        with self.lock:
            self.due.clear()
            self.paths.clear()
