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
import time
from collections import defaultdict
from pathlib import Path

from drive_sync import FOLDER_MIME, md5_of

log = logging.getLogger("drive_sync")
STATE_VERSION = 2


class DriveFolderGone(Exception):
    """The Drive folder being synced was deleted/trashed - refuse to mirror that onto the PC."""


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


class TwoWaySyncer:
    def __init__(self, drive, local_root: Path, remote_root_id: str, *, excludes: list[str],
                 state_path: Path, migrate_from: Path | None = None, on_event=None, recycle=None,
                 progress=None):
        self.drive, self.local_root, self.root_id = drive, local_root, remote_root_id
        self.excludes, self.state_path, self.on_event = excludes, state_path, on_event
        self.progress = progress  # progress.Progress - percentage / current file for the UI
        if recycle is None:
            from send2trash import send2trash as recycle
        self.recycle = recycle
        self.stats = dict(uploaded=0, updated=0, downloaded=0, deleted_local=0, trashed=0, folders=0,
                          conflicts=0, unchanged=0, skipped=0, errors=0)
        self.base: dict[str, dict] = self._load_base(migrate_from)
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

    def _remember_file(self, rel: str, file_id: str, path: Path, md5: str) -> None:
        st = path.stat()
        self.base[rel] = {"t": "f", "id": file_id, "md5": md5, "size": st.st_size, "mtime_ns": st.st_mtime_ns}

    # -- helpers ---------------------------------------------------------------
    def _excluded(self, name: str, rel: str) -> bool:
        import fnmatch
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in self.excludes)

    def _xfer(self, action: str, rel: str, size: int, fn, *args):
        if self.progress:
            self.progress.transfer(action, rel, size)
        try:
            return fn(*args)
        finally:
            if self.progress:
                self.progress.transfer_end()

    def _emit(self, kind: str, rel: str, message: str | None = None) -> None:
        if self.on_event:
            try:
                self.on_event(kind, rel, message)
            except Exception as e:  # noqa: BLE001
                log.debug("Activity hook failed: %s", e)

    def _fail(self, rel: str, what: str, e: Exception) -> None:
        log.error("%s %s: %s", what, rel or str(self.local_root), e)
        self.stats["errors"] += 1
        self._emit("error", rel or ".", f"{what}: {e}")

    def _md5(self, path: Path, b: dict | None) -> str:
        st = path.stat()
        if b and b.get("md5") and b.get("size") == st.st_size and b.get("mtime_ns") == st.st_mtime_ns:
            return b["md5"]  # unchanged since the last sync: no need to read the file
        return md5_of(path)

    @staticmethod
    def _is_syncable_remote(item: dict) -> bool:
        # Google Docs/Sheets/Slides, shortcuts etc. have no file content to download.
        return item["mimeType"] == FOLDER_MIME or "md5Checksum" in item

    # -- main ------------------------------------------------------------------
    def run(self) -> dict:
        start = time.monotonic()
        status = self.drive.folder_status(self.root_id)
        if status != "ok":
            raise DriveFolderGone(
                "The Google Drive folder for this sync was deleted, moved to trash or is no longer "
                "accessible - nothing on this PC was changed. Restore it on Drive, or choose another "
                "Drive folder (Folders → Edit).")
        try:
            self._dir(self.local_root, self.root_id, "")
        finally:
            self._save()
        s = self.stats
        log.info("Done in %.1fs (two-way): %d uploaded, %d updated, %d downloaded, %d to Recycle Bin, "
                 "%d to Drive trash, %d conflicts, %d unchanged, %d errors", time.monotonic() - start,
                 s["uploaded"], s["updated"], s["downloaded"], s["deleted_local"], s["trashed"],
                 s["conflicts"], s["unchanged"], s["errors"])
        return s

    def _dir(self, local_dir: Path, remote_id: str, rel_dir: str) -> None:
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

        for key in sorted(set(local) | set(remote) | set(known)):
            L, R = local.get(key), remote.get(key)
            name = L.name if L else R["name"] if R else known[key]
            rel = _join(rel_dir, name)
            try:
                self._item(L, R, self.base.get(rel), rel, local_dir, remote_id, name)
            except Exception as e:  # noqa: BLE001 - keep going with the other files
                self._fail(rel, "Couldn't sync", e)

    def _item(self, L, R, B, rel, local_dir: Path, remote_id: str, name: str) -> None:
        l_dir = L is not None and L.is_dir()
        r_dir = R is not None and R["mimeType"] == FOLDER_MIME
        if L is not None and R is not None and l_dir != r_dir:
            self.stats["skipped"] += 1
            self._emit("skipped", rel, "A file and a folder have the same name on this PC and on Drive")
            return
        if l_dir or r_dir or (L is None and R is None and B and B.get("t") == "d"):
            self._folder(L, R, B, rel, local_dir / name, remote_id, name)
        else:
            if L is None and R is not None and self.progress:
                self.progress.add_total(1, int(R.get("size") or 0))  # on Drive only: not counted yet
            try:
                self._file(L, R, B, rel, local_dir / name, remote_id)
            finally:
                if self.progress and (L is not None or R is not None):
                    try:
                        size = L.stat().st_size if L is not None else int(R.get("size") or 0)
                    except OSError:  # e.g. just moved to the Recycle Bin
                        size = int((R or {}).get("size") or 0)
                    self.progress.file_done(size)

    # -- folders ------------------------------------------------------------------
    def _folder(self, L, R, B, rel, path: Path, remote_id: str, name: str) -> None:
        if L is not None and R is not None:
            self.base[rel] = {"t": "d", "id": R["id"]}
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
        """Anything in this Drive folder that wasn't there (or was different) at the last sync?"""
        for item in self.drive.list_children(folder_id):
            r = _join(rel, item["name"])
            if self._excluded(item["name"], r):
                continue
            if item["mimeType"] == FOLDER_MIME:
                if r not in self.base or self._remote_has_new(item["id"], r):
                    return True
            elif "md5Checksum" not in item:
                return True  # a Google Doc etc. lives here - don't trash it with the folder
            elif (self.base.get(r) or {}).get("md5") != item["md5Checksum"]:
                return True
        return False

    # -- files ------------------------------------------------------------------------
    def _file(self, L, R, B, rel, path: Path, remote_id: str) -> None:
        if L is not None and R is not None:
            lmd5, rmd5 = self._md5(path, B), R["md5Checksum"]
            if lmd5 == rmd5:
                self.stats["unchanged"] += 1
                self._remember_file(rel, R["id"], path, lmd5)
                return
            l_changed = not B or B.get("md5") != lmd5
            r_changed = not B or B.get("md5") != rmd5
            if l_changed and not r_changed:
                self._xfer("update", rel, path.stat().st_size, self.drive.update, R["id"], path)
                self.stats["updated"] += 1
                self._emit("updated", rel)
                self._remember_file(rel, R["id"], path, lmd5)
            elif r_changed and not l_changed:
                self._xfer("download", rel, int(R.get("size") or 0), self.drive.download, R["id"], path)
                self.stats["downloaded"] += 1
                self._emit("downloaded", rel, "Changed on Google Drive")
                self._remember_file(rel, R["id"], path, rmd5)
            else:
                self._conflict(R, rel, path, remote_id, rmd5)
        elif L is not None:                                    # on PC only
            lmd5 = self._md5(path, B)
            if B and B.get("md5") == lmd5:
                self.recycle(str(path))                        # deleted on Drive
                self.stats["deleted_local"] += 1
                self._emit("deleted_local", rel, "Deleted on Google Drive")
                self._forget(rel)
                return
            file_id = self._xfer("upload", rel, path.stat().st_size, self.drive.upload, path, remote_id)
            self.stats["uploaded"] += 1
            self._emit("uploaded", rel, "Kept: deleted on Drive, but changed on this PC" if B else None)
            self._remember_file(rel, file_id, path, lmd5)
        elif R is not None:                                    # on Drive only
            rmd5 = R["md5Checksum"]
            if B and B.get("md5") == rmd5:
                self.drive.trash(R["id"])                      # deleted on PC
                self.stats["trashed"] += 1
                self._emit("trashed", rel, "Deleted on this PC")
                self._forget(rel)
                return
            self._xfer("download", rel, int(R.get("size") or 0), self.drive.download, R["id"], path)
            self.stats["downloaded"] += 1
            self._emit("downloaded", rel, "Kept: deleted on this PC, but changed on Drive" if B
                       else "New on Google Drive")
            self._remember_file(rel, R["id"], path, rmd5)
        else:
            self._forget(rel)

    def _conflict(self, R: dict, rel: str, path: Path, remote_id: str, rmd5: str) -> None:
        """Edited on both sides: Drive's version keeps the name, the PC version is kept beside it."""
        stamp = time.strftime("%Y-%m-%d %H%M")
        keep = path.with_name(f"{path.stem} (conflict {stamp} PC){path.suffix}")
        os.replace(path, keep)
        try:
            self._xfer("download", rel, int(R.get("size") or 0), self.drive.download, R["id"], path)
        except Exception:
            os.replace(keep, path)  # put the PC version back; try again next time
            raise
        self._remember_file(rel, R["id"], path, rmd5)
        keep_rel = _join(_parent(rel), keep.name)
        keep_id = self._xfer("upload", keep_rel, keep.stat().st_size, self.drive.upload, keep, remote_id)
        self._remember_file(keep_rel, keep_id, keep, md5_of(keep))
        self.stats["conflicts"] += 1
        self._emit("conflict", rel, f"Edited on both this PC and Drive. Drive's version kept this name; "
                                    f"your PC version was saved as “{keep.name}” (on both sides).")
