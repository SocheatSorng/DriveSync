"""
Two-way sync between a PC folder and a Google Drive folder.

For every file and folder we compare three things:
    L = what's on this PC now
    R = what's on Google Drive now
    B = what both sides looked like after the last successful sync ("base")

and decide:
    only L changed            -> upload            only R changed          -> download
    both changed differently  -> keep both (the PC copy is renamed "... (conflict <date> PC)")
    new on one side (no B)    -> copy to the other side
    deleted on Drive (B, no R, L unchanged) -> move the PC copy to the Windows Recycle Bin
    deleted on PC    (B, no L, R unchanged) -> move the Drive copy to the Drive trash
    deleted on one side but edited on the other -> keep the edited file (copy it back)

Nothing is ever deleted permanently. If the Drive folder itself has been deleted or
trashed, the sync stops before touching anything on this PC.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from downloader import PARALLEL
from drive_sync import FOLDER_MIME, NotEnoughSpace, StorageFullMixin, log_event, md5_of
from progress import Paused

DISK_MARGIN = 200 * 1024 * 1024   # keep this much free on the PC's disk after a download

log = logging.getLogger("drive_sync")
STATE_VERSION = 2
PARALLEL_DOWNLOADS = PARALLEL  # one setting, shared with the tray menu's file lines
QUEUED = object()  # _file() result: a download was started in the background
DEFERRED = object()  # _file() result: a new PC file, sent at the end (it may turn out to be a move)
SAVE_EVERY = 30.0  # seconds between saves of the sync state during a long sync


class _Done:
    """A finished 'future' for running a download synchronously (same interface as concurrent.futures)."""

    def __init__(self, fn):
        self._error, self._value = None, None
        try:
            self._value = fn()
        except Exception as e:  # noqa: BLE001 - re-raised by result()
            self._error = e

    def done(self) -> bool:
        return True

    def result(self):
        if self._error:
            raise self._error
        return self._value


class DriveFolderGone(Exception):
    """The Drive folder being synced was deleted/trashed - refuse to mirror that onto the PC."""


class ChangedOnPC(Exception):
    """The PC copy was saved while Drive's version was downloading: it's kept, not overwritten."""


def _sig(path: Path) -> tuple[int, int] | None:
    """(size, modified time) of a file, or None if it's gone - enough to tell that it was saved."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _join(rel_dir: str, name: str) -> str:
    return f"{rel_dir}/{name}" if rel_dir else name


def _parent(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _birth(st: os.stat_result) -> float:
    return getattr(st, "st_birthtime", st.st_ctime)


def migration_base(local_root: Path, old_cache: dict, last_sync: float, excluded) -> dict:
    """Base for a folder that used upload-only sync before.

    Upload-only sync copied every local item to Drive, so anything that already existed
    on this PC at the last sync was on Drive then. Files take their checksum from the old
    cache; folders count if they were created before the last sync.
    """
    base: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(local_root):
        rel_dir = Path(dirpath).relative_to(local_root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames if not excluded(d, _join(rel_dir, d))]
        for d in dirnames:
            try:
                if _birth(os.stat(os.path.join(dirpath, d))) <= last_sync:
                    base[_join(rel_dir, d)] = {"t": "d"}
            except OSError:
                pass
        for f in filenames:
            rel = _join(rel_dir, f)
            old = old_cache.get(rel) or old_cache.get(rel.replace("/", os.sep))
            if old and not excluded(f, rel):
                base[rel] = {"t": "f", "md5": old["md5"], "size": old["size"], "mtime_ns": old["mtime_ns"]}
    return base


class TwoWaySyncer(StorageFullMixin):
    def __init__(self, drive, local_root: Path, remote_root_id: str, *, excludes: list[str],
                 state_path: Path, migrate_from: Path | None = None, on_event=None, recycle=None,
                 progress=None, parallel: int = PARALLEL_DOWNLOADS):
        self.drive, self.local_root, self.root_id = drive, local_root, remote_root_id
        self.parallel = max(1, parallel)  # downloads at once (measured ~4x faster for many small files)
        self.pool: ThreadPoolExecutor | None = None
        self.pending: list = []           # (future, job) of downloads not yet recorded
        self.count_as_we_go = False       # quick sync: add each folder's files to the progress total
        self._last_save = time.monotonic()
        self.excludes, self.state_path, self.on_event = excludes, state_path, on_event
        self.progress = progress  # progress.Progress - percentage / current file for the UI
        if recycle is None:
            from send2trash import send2trash as recycle
        self.recycle = recycle
        self.stats = dict(uploaded=0, updated=0, downloaded=0, deleted_local=0, trashed=0, folders=0,
                          conflicts=0, unchanged=0, skipped=0, errors=0, moved=0)
        self.full: set[str] = set()   # "drive" / "pc" once that side is known to be full
        # Renamed / moved on this PC = new there + gone from its old place. Both are kept until the
        # end of the sync, so a match is moved on Drive instead of uploaded again.
        self.can_move = hasattr(drive, "move")     # (turned off below for a first sync: nothing to move)
        self.new_on_pc: list = []          # (rel, path, parent_id, md5, sig)
        self.gone_files: list = []         # (rel, R, B) files deleted on this PC
        self.gone_dirs: list = []          # (rel, R) folders deleted on this PC
        self._by_size: dict[int, list[str]] | None = None   # synced files by size (see _could_be_moved)
        self.base: dict[str, dict] = self._load_base(migrate_from)
        self.can_move = self.can_move and bool(self.base)
        self.children: dict[str, set[str]] = defaultdict(set)
        for rel in self.base:
            self.children[_parent(rel)].add(rel.rsplit("/", 1)[-1])

    # -- state ----------------------------------------------------------------
    def _load_base(self, migrate_from: Path | None) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("version") == STATE_VERSION and data.get("root") == self.root_id:
                return data.get("base", {})
        except (OSError, ValueError):
            pass
        if migrate_from and migrate_from.exists():
            try:
                old = json.loads(migrate_from.read_text(encoding="utf-8"))
                base = migration_base(self.local_root, old, migrate_from.stat().st_mtime, self._excluded)
                log.info("Switched %s to two-way sync (%d items known from earlier syncs)", self.local_root, len(base))
                return base
            except (OSError, ValueError, KeyError) as e:
                log.warning("Couldn't read the earlier sync state (%s) - starting fresh", e)
        return {}  # first sync: nothing is deleted, both sides are merged

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": STATE_VERSION, "root": self.root_id, "base": self.base}),
                       encoding="utf-8")
        tmp.replace(self.state_path)

    def _forget(self, rel: str) -> None:
        prefix = rel + "/"
        for k in [k for k in self.base if k == rel or k.startswith(prefix)]:
            del self.base[k]

    def _remember_file(self, rel: str, file_id: str, md5: str, sig: tuple[int, int] | None) -> None:
        """Record a synced file. sig is the PC copy's (size, modified time) *when md5 was taken*: a
        later save then never pairs with the old checksum, so the next sync sees it as changed."""
        size, mtime_ns = sig or (-1, -1)  # gone already: the next sync works out what happened
        self.base[rel] = {"t": "f", "id": file_id, "md5": md5, "size": size, "mtime_ns": mtime_ns}

    def _remember_sent(self, rel: str, file_id: str, parent_id: str, path: Path, md5: str,
                       sig: tuple[int, int] | None) -> None:
        """Record a file just uploaded (md5/sig: the PC copy before sending). If it was saved again
        while uploading, Drive may hold the old version - or, for a big file sent in pieces, a mix of
        both - so remember what Drive really has. The next sync then sends the newer PC copy."""
        if _sig(path) == sig:
            return self._remember_file(rel, file_id, md5, sig)
        log.info("%s changed while it was uploading - it will be sent again", rel)
        try:
            remote = next((c for c in self.drive.list_children(parent_id) if c["id"] == file_id), {})
            md5 = remote.get("md5Checksum") or md5
        except Exception as e:  # noqa: BLE001 - fall back to the checksum that was meant to be sent
            log.warning("Couldn't check what Drive received for %s: %s", rel, e)
        self._remember_file(rel, file_id, md5, sig)

    # -- helpers ---------------------------------------------------------------
    def _excluded(self, name: str, rel: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in self.excludes)

    def _in_skipped(self, rel: str) -> bool:
        """Is this path skipped, or inside a skipped folder? A quick sync goes straight to changed
        folders, so a folder skipped after it was synced must not be entered from below."""
        parts = [p for p in rel.split("/") if p]
        return any(self._excluded(parts[i], "/".join(parts[:i + 1])) for i in range(len(parts)))

    def _xfer(self, action: str, rel: str, size: int, fn, *args):
        self._check_full(action)
        if self.progress:
            self.progress.transfer(action, rel, size)
        try:
            return fn(*args)
        finally:
            if self.progress:
                self.progress.transfer_end()

    def _check_disk(self, size: int) -> None:
        """Don't start a download that can't fit on this PC (leaves a little room for Windows)."""
        if size < 1024 * 1024:
            return
        try:
            free = shutil.disk_usage(self.local_root).free
        except OSError:
            return
        if size + DISK_MARGIN > free:
            from progress import human_size
            raise NotEnoughSpace(f"Not enough space on this PC for this file ({human_size(size)}; "
                                 f"{human_size(free)} free)")

    def _emit(self, kind: str, rel: str, message: str | None = None) -> None:
        log_event(kind, rel, message)
        if self.on_event:
            try:
                self.on_event(kind, rel, message)
            except Exception as e:  # noqa: BLE001
                log.debug("Activity hook failed: %s", e)

    def _fail(self, rel: str, what: str, e: Exception) -> None:
        if self._fail_full(rel, e):
            return
        log.error("%s %s: %s", what, rel or str(self.local_root), e)
        self.stats["errors"] += 1
        self._emit("error", rel or ".", f"{what}: {e}")

    def _md5(self, path: Path, b: dict | None) -> str:
        return self._hash(path, b)[0]

    def _hash(self, path: Path, b: dict | None) -> tuple[str, tuple[int, int]]:
        """(md5, (size, modified time)) - the size/time read *before* the file, so a save made
        while it's being read or sent shows up as a change next time."""
        st = path.stat()
        sig = (st.st_size, st.st_mtime_ns)
        if b and b.get("md5") and (b.get("size"), b.get("mtime_ns")) == sig:
            return b["md5"], sig  # unchanged since the last sync: no need to read the file
        if not self.progress or st.st_size < 16 * 1024 * 1024:
            return md5_of(path), sig
        self.progress.read_start(path.relative_to(self.local_root).as_posix())
        try:
            return md5_of(path), sig
        finally:
            self.progress.read_end()

    @staticmethod
    def _is_syncable_remote(item: dict) -> bool:
        # Google Docs/Sheets/Slides, shortcuts etc. have no file content to download.
        return item["mimeType"] == FOLDER_MIME or "md5Checksum" in item

    # -- quick sync: find what changed --------------------------------------------
    def _known_dir(self, rel: str) -> str | None:
        """The deepest folder of `rel` that was synced before and still exists on this PC
        ("" = the sync folder itself). None if that folder's Drive id isn't known (old state)."""
        cur = ""
        for part in [p for p in rel.split("/") if p and p != "."]:
            match = next((n for n in self.children.get(cur, ()) if n.casefold() == part.casefold()
                          and (self.base.get(_join(cur, n)) or {}).get("t") == "d"), None)
            if match is None or not (self.local_root / _join(cur, match)).is_dir():
                break
            cur = _join(cur, match)
        if cur and not self.base[cur].get("id"):
            return None
        return cur

    def scan_local(self) -> set[str]:
        """Folders whose contents on this PC differ from the last sync. Compares names, sizes and
        modified times only (nothing is read), so it takes seconds even for many thousands of files."""
        changed: set[str] = set()

        def walk(rel_dir: str, path: str) -> None:
            known = {n.casefold(): n for n in self.children.get(rel_dir, ())}
            seen = set()
            try:
                entries = list(os.scandir(path))
            except OSError:
                changed.add(rel_dir)
                return
            for e in entries:
                if e.is_symlink() or self._excluded(e.name, _join(rel_dir, e.name)):
                    continue
                key = e.name.casefold()
                seen.add(key)
                rel = _join(rel_dir, known.get(key, e.name))
                b = self.base.get(rel) if key in known else None
                try:
                    is_dir = e.is_dir()
                    if b is None or (b.get("t") == "d") != is_dir:
                        changed.add(rel_dir)                       # new here, or file <-> folder
                    elif is_dir:
                        walk(rel, e.path)
                    else:
                        st = e.stat()
                        if st.st_size != b.get("size") or st.st_mtime_ns != b.get("mtime_ns"):
                            changed.add(rel_dir)
                except OSError:
                    changed.add(rel_dir)
            if set(known) - seen:
                changed.add(rel_dir)                               # something was deleted here

        walk("", str(self.local_root))
        return changed

    def targets_for(self, paths=(), drive_ids=(), scan: bool = False) -> set[str] | None:
        """The folders a quick sync has to look at, or None if everything must be checked
        (first sync, or sync state from an older version)."""
        if not self.base or any(b.get("t") == "d" and not b.get("id") for b in self.base.values()):
            return None                     # never synced, or state from upload-only mode: check it all
        found = set(self.scan_local()) if scan else set()
        root = os.path.normcase(os.path.abspath(self.local_root))
        for p in paths:                     # changed on this PC: look at the folder that holds it
            ap = os.path.abspath(p)
            if os.path.normcase(ap) == root:
                found.add("")
            elif os.path.normcase(ap).startswith(root + os.sep):
                found.add(_parent(os.path.relpath(ap, self.local_root).replace(os.sep, "/")))
        by_id = {b["id"]: rel for rel, b in self.base.items() if b.get("id")}
        by_id[self.root_id] = ""
        for i in drive_ids:                 # changed on Drive (files, and the folders holding them)
            rel = by_id.get(i)
            if rel is None:
                continue                    # not in this sync folder (or new: its folder is listed too)
            if rel == "":
                found.add("")
            else:
                found.add(_parent(rel))     # renamed / moved / trashed: its folder sees that
                if self.base[rel].get("t") == "d":
                    found.add(rel)
        targets = set()
        for rel in found:
            k = self._known_dir(rel)
            if k is None:
                return None
            targets.add(k)
        return targets

    # -- main ------------------------------------------------------------------
    def run(self, targets: set[str] | None = None) -> dict:
        """Sync everything, or (targets) only those folders - each one's own files and subfolders;
        a subfolder is only gone into when it is new or deleted on one side."""
        start = time.monotonic()
        status = self.drive.folder_status(self.root_id)
        if status != "ok":
            raise DriveFolderGone(
                "The Google Drive folder for this sync was deleted, moved to trash or is no longer "
                "accessible - nothing on this PC was changed. Restore it on Drive, or choose another "
                "Drive folder (Folders → Edit).")
        self.count_as_we_go = targets is not None   # a full sync counted its files up front
        try:
            with ThreadPoolExecutor(max_workers=self.parallel, thread_name_prefix="download") as pool:
                self.pool = pool
                try:
                    if targets is None:
                        self._dir(self.local_root, self.root_id, "")
                    else:
                        # Parents first: they may delete or replace a folder that is also listed.
                        for rel in sorted(targets, key=lambda r: (r.count("/") if r else -1, r)):
                            b = self.base.get(rel) if rel else {"t": "d", "id": self.root_id}
                            path = self.local_root / rel if rel else self.local_root
                            if not b or b.get("t") != "d" or not b.get("id") or not path.is_dir():
                                continue            # already handled while syncing its parent
                            self._dir(path, b["id"], rel, deep=False)
                    self._settle_moves()
                finally:
                    self._finish_downloads(wait=True)   # record every download before saving state
        finally:
            self.pool = None
            self._save()
        s = self.stats
        log.info("Done in %.1fs (two-way, %s): %d uploaded, %d updated, %d downloaded, %d to Recycle Bin, "
                 "%d to Drive trash, %d conflicts, %d unchanged, %d errors", time.monotonic() - start,
                 "everything" if targets is None else f"{len(targets)} changed folder(s)",
                 s["uploaded"], s["updated"], s["downloaded"], s["deleted_local"], s["trashed"],
                 s["conflicts"], s["unchanged"], s["errors"])
        return s

    def _dir(self, local_dir: Path, remote_id: str, rel_dir: str, deep: bool = True) -> None:
        if rel_dir and self._in_skipped(rel_dir):
            return                                  # skipped: left alone on both sides
        try:
            local = {}
            for e in os.scandir(local_dir):
                rel = _join(rel_dir, e.name)
                if e.is_symlink() or self._excluded(e.name, rel):
                    continue
                local[e.name.casefold()] = e
            remote = {}
            for item in self.drive.list_children(remote_id):
                if not self._is_syncable_remote(item) or self._excluded(item["name"], _join(rel_dir, item["name"])):
                    continue
                remote.setdefault(item["name"].casefold(), item)  # duplicates on Drive: use the first
        except Exception as e:  # noqa: BLE001
            self._fail(rel_dir, "Couldn't read folder", e)
            return
        known = {n.casefold(): n for n in self.children.get(rel_dir, ())}
        if self.count_as_we_go and self.progress:
            sizes = []
            for e in local.values():
                try:
                    if not e.is_dir():
                        sizes.append(e.stat().st_size)
                except OSError:
                    sizes.append(0)
            self.progress.add_total(len(sizes), sum(sizes))

        for key in sorted(set(local) | set(remote) | set(known)):
            L, R = local.get(key), remote.get(key)
            name = L.name if L else R["name"] if R else known[key]
            rel = _join(rel_dir, name)
            try:
                self._item(L, R, self.base.get(rel), rel, local_dir, remote_id, name, deep)
            except Exception as e:  # noqa: BLE001 - keep going with the other files
                self._fail(rel, "Couldn't sync", e)
            self._save_now_and_then()

    def _save_now_and_then(self) -> None:
        """Save progress during a long sync, so a restart or crash doesn't lose it (each entry only
        records an item that is the same on both sides, so a partly saved state is still correct)."""
        now = time.monotonic()
        if now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            try:
                self._save()
            except OSError as e:
                log.debug("Couldn't save sync progress: %s", e)

    def _item(self, L, R, B, rel, local_dir: Path, remote_id: str, name: str, deep: bool = True) -> None:
        l_dir = L is not None and L.is_dir()
        r_dir = R is not None and R["mimeType"] == FOLDER_MIME
        if L is not None and R is not None and l_dir != r_dir:
            self.stats["skipped"] += 1
            self._emit("skipped", rel, "A file and a folder have the same name on this PC and on Drive")
            return
        if l_dir or r_dir or (L is None and R is None and B and B.get("t") == "d"):
            self._folder(L, R, B, rel, local_dir / name, remote_id, name, deep)
        else:
            if L is None and R is not None and self.progress:
                self.progress.add_total(1, int(R.get("size") or 0))  # on Drive only: not counted yet
            queued = False
            try:
                queued = self._file(L, R, B, rel, local_dir / name, remote_id) in (QUEUED, DEFERRED)
            finally:
                # A queued download is counted when it finishes (in _finish_downloads).
                if self.progress and not queued and (L is not None or R is not None):
                    try:
                        size = L.stat().st_size if L is not None else int(R.get("size") or 0)
                    except OSError:  # e.g. just moved to the Recycle Bin
                        size = int((R or {}).get("size") or 0)
                    self.progress.file_done(size)

    # -- folders ------------------------------------------------------------------
    def _folder(self, L, R, B, rel, path: Path, remote_id: str, name: str, deep: bool = True) -> None:
        if L is not None and R is not None:
            self.base[rel] = {"t": "d", "id": R["id"]}
            if deep or not B or B.get("t") != "d":   # quick sync: a known folder is checked only if listed
                self._dir(path, R["id"], rel)
        elif L is not None:                                   # on PC only
            if B and not self._local_has_new(path, rel):
                self.recycle(str(path))                       # deleted on Drive
                self.stats["deleted_local"] += 1
                self._emit("deleted_local", rel, "Deleted on Google Drive")
                self._forget(rel)
                return
            folder_id = self.drive.create_folder(name, remote_id)
            self.stats["folders"] += 1
            self._emit("folder", rel, "Kept: deleted on Drive, but has new files on this PC" if B else None)
            self.base[rel] = {"t": "d", "id": folder_id}
            self._dir(path, folder_id, rel)
        elif R is not None:                                   # on Drive only
            if B and not self._remote_has_new(R["id"], rel):
                if self.can_move:
                    self.gone_dirs.append((rel, R))           # trashed at the end (after any moves out)
                    return
                self.drive.trash(R["id"])                     # deleted on PC
                self.stats["trashed"] += 1
                self._emit("trashed", rel, "Deleted on this PC")
                self._forget(rel)
                return
            path.mkdir(exist_ok=True)
            self.stats["folders"] += 1
            self._emit("folder", rel, "Kept: deleted on this PC, but has new files on Drive" if B
                       else "Created on this PC")
            self.base[rel] = {"t": "d", "id": R["id"]}
            self._dir(path, R["id"], rel)
        else:
            self._forget(rel)                                 # gone on both sides

    def _local_has_new(self, path: Path, rel: str) -> bool:
        """Anything in this PC folder that wasn't there (or was different) at the last sync?"""
        for dirpath, dirnames, filenames in os.walk(path):
            sub = Path(dirpath).relative_to(self.local_root).as_posix()
            dirnames[:] = [d for d in dirnames if not self._excluded(d, f"{sub}/{d}")]
            for d in dirnames:
                if f"{sub}/{d}" not in self.base:
                    return True
            for f in filenames:
                r = f"{sub}/{f}"
                if self._excluded(f, r):
                    continue
                b = self.base.get(r)
                if not b or self._md5(Path(dirpath) / f, b) != b.get("md5"):
                    return True
        return False

    def _remote_has_new(self, folder_id: str, rel: str) -> bool:
        """Anything in this Drive folder that wasn't there (or was different) at the last sync?

        Looked at level by level, asking about up to 50 folders per request: a deleted folder with
        thousands of subfolders (e.g. a Python .venv) took one request each - many minutes with the
        status stuck at "Checking files"."""
        level = {folder_id: rel}            # Drive folder id -> its path
        seen = 0
        try:
            while level:
                seen += len(level)
                if self.progress:
                    name = rel.rsplit("/", 1)[-1]
                    self.progress.read_start(f"“{name}” on Google Drive (deleted on this PC) · {seen:,} folders")
                items = self._children_of(list(level))
                nxt = {}
                for item in items:
                    r = _join(level[item["_parent"]], item["name"])
                    if self._excluded(item["name"], r):
                        continue
                    if item["mimeType"] == FOLDER_MIME:
                        if r not in self.base:
                            return True
                        nxt[item["id"]] = r
                    elif "md5Checksum" not in item:
                        return True  # a Google Doc etc. lives here - don't trash it with the folder
                    elif (self.base.get(r) or {}).get("md5") != item["md5Checksum"]:
                        return True
                level = nxt
            return False
        finally:
            if self.progress:
                self.progress.read_end()

    def _children_of(self, folder_ids: list[str]) -> list[dict]:
        """Items in these Drive folders, each marked with "_parent" (which of them it's in)."""
        wanted = set(folder_ids)
        if hasattr(self.drive, "list_children_of"):
            out = []
            for item in self.drive.list_children_of(folder_ids):
                parent = next((p for p in item.get("parents", ()) if p in wanted), None)
                if parent:
                    out.append({**item, "_parent": parent})
            return out
        return [{**item, "_parent": f} for f in folder_ids for item in self.drive.list_children(f)]

    # -- files ------------------------------------------------------------------------
    def _file(self, L, R, B, rel, path: Path, remote_id: str) -> None:
        if L is not None and R is not None:
            (lmd5, sig), rmd5 = self._hash(path, B), R["md5Checksum"]
            if lmd5 == rmd5:
                self.stats["unchanged"] += 1
                self._remember_file(rel, R["id"], lmd5, sig)
                return
            l_changed = not B or B.get("md5") != lmd5
            r_changed = not B or B.get("md5") != rmd5
            if l_changed and not r_changed:
                self._xfer("update", rel, sig[0], self.drive.update, R["id"], path)
                self.stats["updated"] += 1
                self._emit("updated", rel)
                self._remember_sent(rel, R["id"], remote_id, path, lmd5, sig)
            elif r_changed and not l_changed:
                return self._queue_download(R, rel, path, "Changed on Google Drive", sig)
            else:
                self._conflict(R, rel, path, remote_id, rmd5)
        elif L is not None:                                    # on PC only
            lmd5, sig = self._hash(path, B)
            if B and B.get("md5") == lmd5:
                self.recycle(str(path))                        # deleted on Drive
                self.stats["deleted_local"] += 1
                self._emit("deleted_local", rel, "Deleted on Google Drive")
                self._forget(rel)
                return
            if not B and self.can_move and self._could_be_moved(sig[0]):
                self.new_on_pc.append((rel, path, remote_id, lmd5, sig))
                return DEFERRED
            self._upload_new(rel, path, remote_id, lmd5, sig,
                             "Kept: deleted on Drive, but changed on this PC" if B else None)
        elif R is not None:                                    # on Drive only
            rmd5 = R["md5Checksum"]
            if B and B.get("md5") == rmd5:
                if self.can_move:
                    self.gone_files.append((rel, R, B))        # trashed at the end, unless it moved
                    return
                self.drive.trash(R["id"])                      # deleted on PC
                self.stats["trashed"] += 1
                self._emit("trashed", rel, "Deleted on this PC")
                self._forget(rel)
                return
            return self._queue_download(R, rel, path, "Kept: deleted on this PC, but changed on Drive" if B
                                        else "New on Google Drive", _sig(path))
        else:
            self._forget(rel)

    def _could_be_moved(self, size: int) -> bool:
        """Could a new PC file of this size be a renamed / moved one? Only if a synced file of the same
        size is missing from its place on this PC. Everything else uploads right away (holding back
        every new file until the end would leave a big new folder at "Checking files" for ages)."""
        if size <= 0:
            return False
        if self._by_size is None:
            self._by_size = defaultdict(list)
            for rel, b in self.base.items():
                if b.get("t") == "f" and (b.get("size") or 0) > 0:
                    self._by_size[b["size"]].append(rel)
        return any(not (self.local_root / r).exists() for r in self._by_size.get(size, ()))

    def _upload_new(self, rel: str, path: Path, remote_id: str, lmd5: str, sig, message: str | None = None):
        file_id = self._xfer("upload", rel, sig[0], self.drive.upload, path, remote_id)
        self.stats["uploaded"] += 1
        self._emit("uploaded", rel, message)
        self._remember_sent(rel, file_id, remote_id, path, lmd5, sig)

    def _settle_moves(self) -> None:
        """New files on this PC that match one that's gone from its old place (same content and size)
        were renamed or moved: do the same on Drive instead of uploading again. Then trash what's
        really gone."""
        old: dict[tuple, list] = defaultdict(list)    # (md5, size) -> [(old rel, Drive id)]
        for rel, R, B in self.gone_files:
            old[(B.get("md5"), B.get("size"))].append((rel, R["id"]))
        for rel, _R in self.gone_dirs:
            prefix = rel + "/"
            for k, b in self.base.items():
                if k.startswith(prefix) and b.get("t") == "f" and b.get("id"):
                    old[(b.get("md5"), b.get("size"))].append((k, b["id"]))
        moved: set[str] = set()
        new, self.new_on_pc = self.new_on_pc, []
        for rel, path, parent_id, lmd5, sig in new:
            try:
                match = old.get((lmd5, sig[0])) if sig[0] > 0 else None   # empty files all look alike
                if match:
                    old_rel, file_id = match.pop(0)
                    self.drive.move(file_id, rel.rsplit("/", 1)[-1], parent_id)
                    moved.add(file_id)
                    self._forget(old_rel)
                    self._remember_sent(rel, file_id, parent_id, path, lmd5, sig)
                    self.stats["moved"] += 1
                    how = ("Renamed on Drive too" if _parent(old_rel) == _parent(rel) else "Moved on Drive too")
                    self._emit("moved", rel, f"{how} (was {old_rel}) - not uploaded again")
                else:
                    self._upload_new(rel, path, parent_id, lmd5, sig)
            except Exception as e:  # noqa: BLE001 - keep going with the other files
                self._fail(rel, "Couldn't sync", e)
            finally:
                if self.progress:
                    self.progress.file_done(sig[0])
            self._save_now_and_then()
        gone_files, self.gone_files = self.gone_files, []
        for rel, R, _B in gone_files:
            if R["id"] in moved:
                continue
            try:
                self.drive.trash(R["id"])                      # deleted on PC
                self.stats["trashed"] += 1
                self._emit("trashed", rel, "Deleted on this PC")
                self._forget(rel)
            except Exception as e:  # noqa: BLE001
                self._fail(rel, "Couldn't sync", e)
        gone_dirs, self.gone_dirs = self.gone_dirs, []
        for rel, R in gone_dirs:
            try:
                self.drive.trash(R["id"])                      # deleted on PC (moved files went out first)
                self.stats["trashed"] += 1
                self._emit("trashed", rel, "Deleted on this PC")
                self._forget(rel)
            except Exception as e:  # noqa: BLE001
                self._fail(rel, "Couldn't sync", e)

    # -- parallel downloads ---------------------------------------------------------------
    def _queue_download(self, R: dict, rel: str, path: Path, message: str, sig: tuple[int, int] | None):
        """Start downloading in the background (up to `parallel` at once). Everything that
        changes the saved sync state happens later, back on this thread, in _finish_downloads.
        sig: the PC copy's (size, modified time) when the download was decided (None: no PC copy).
        If it's saved meanwhile, the download isn't used - the next sync sees both sides changed."""
        size = int(R.get("size") or 0)
        self._check_full("download")
        self._check_disk(size)

        def still_as_decided():
            if _sig(path) != sig:
                raise ChangedOnPC()

        def work():
            if self.progress:
                self.progress.transfer("download", rel, size)
            try:
                self.drive.download(R["id"], path, expected_md5=R["md5Checksum"], size=size,
                                    on_bytes=(lambda got: self.progress.transfer_bytes(rel, got))
                                    if self.progress else None, before_replace=still_as_decided)
                return _sig(path)  # right away: a save after this is a new change
            finally:
                if self.progress:
                    self.progress.transfer_end(rel)

        job = (rel, path, R, message, size)
        if self.pool is None:                 # no pool (e.g. a single file): download right here
            self.pending.append((_Done(work), job))
        else:
            self.pending.append((self.pool.submit(work), job))
            self._finish_downloads(wait=False)
        return QUEUED

    def _finish_downloads(self, wait: bool = True) -> None:
        """Record finished downloads (state, Activity, progress). wait=False: only the ones already done."""
        still, paused = [], False
        for fut, (rel, path, R, message, size) in self.pending:
            if not wait and not fut.done():
                still.append((fut, (rel, path, R, message, size)))
                continue
            try:
                got = fut.result()
                self.stats["downloaded"] += 1
                self._emit("downloaded", rel, message)
                self._remember_file(rel, R["id"], R["md5Checksum"], got)
            except Paused:      # not recorded: it's downloaded again when syncing resumes
                paused = True
                continue
            except ChangedOnPC:
                log.info("%s was saved on this PC while Drive's version was downloading - kept yours", rel)
                self.stats["skipped"] += 1
                self._emit("skipped", rel, "Saved on this PC while Drive's version was downloading - "
                                           "yours was kept; it's checked again next sync")
            except Exception as e:  # noqa: BLE001 - the other downloads carry on
                self._fail(rel, "Couldn't download", e)
            finally:
                if self.progress:
                    self.progress.file_done(size)
        self.pending = still
        if paused:
            raise Paused()

    def _conflict(self, R: dict, rel: str, path: Path, remote_id: str, rmd5: str) -> None:
        """Edited on both sides: Drive's version keeps the name, the PC version is kept beside it."""
        stamp = time.strftime("%Y-%m-%d %H%M")
        keep = path.with_name(f"{path.stem} (conflict {stamp} PC){path.suffix}")
        os.replace(path, keep)
        try:
            self._xfer("download", rel, int(R.get("size") or 0),
                       lambda: self.drive.download(R["id"], path, expected_md5=rmd5,
                                                   size=int(R.get("size") or 0) or None))
        except Exception:
            os.replace(keep, path)  # put the PC version back; try again next time
            raise
        self._remember_file(rel, R["id"], rmd5, _sig(path))
        keep_rel = _join(_parent(rel), keep.name)
        keep_md5, keep_sig = self._hash(keep, None)
        keep_id = self._xfer("upload", keep_rel, keep_sig[0], self.drive.upload, keep, remote_id)
        self._remember_sent(keep_rel, keep_id, remote_id, keep, keep_md5, keep_sig)
        self.stats["conflicts"] += 1
        self._emit("conflict", rel, f"Edited on both this PC and Drive. Drive's version kept this name; "
                                    f"your PC version was saved as “{keep.name}” (on both sides).")
