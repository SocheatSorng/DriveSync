"""What's using my space: on this PC (any folders) and on Google Drive (a whole account).

Finds the biggest folders and files, duplicate copies, junk files and - on Drive - old versions and the
trash. Suggests what's safe to delete. Deleting always goes to the Recycle Bin / Drive trash, except
old versions on Drive, which Google can only remove for good (the window asks first).
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("drive_sync")

FOLDER_MIME = "application/vnd.google-apps.folder"
# Files nobody needs: Mac leftovers, Windows thumbnails, Office lock files, unfinished downloads.
JUNK = ["__MACOSX", "._*", ".DS_Store", ".Spotlight-V100", ".Trashes", ".fseventsd", "Thumbs.db",
        "desktop.ini", "~$*", "*.tmp", "*.crdownload", "~drivesync-*"]
JUNK_DIRS = {"__MACOSX", ".Spotlight-V100", ".Trashes", ".fseventsd"}
TOP = 30                 # biggest folders / files listed
MIN_DUPLICATE = 64 * 1024  # smaller duplicates aren't worth showing
OLD_VERSIONS_MIN = 1024 * 1024  # only list files whose old versions use at least this much


def is_junk(name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in JUNK)


@dataclass
class Item:
    """A file or folder found by a scan. `key` is what deleting needs: a PC path or a Drive file id."""
    key: str
    path: str                 # shown to the user
    size: int
    modified: float = 0.0
    is_dir: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class DuplicateGroup:
    size: int                 # of one copy
    items: list[Item]         # the first one is the copy to keep

    @property
    def wasted(self) -> int:
        return self.size * (len(self.items) - 1)


@dataclass
class Report:
    where: str                                   # "pc" or "drive"
    title: str                                   # "D:\\EBC" or "you@example.com"
    total: int = 0                               # bytes looked at
    files: int = 0
    biggest_folders: list[Item] = field(default_factory=list)
    biggest_files: list[Item] = field(default_factory=list)
    duplicates: list[DuplicateGroup] = field(default_factory=list)
    junk: list[Item] = field(default_factory=list)
    old_versions: list[Item] = field(default_factory=list)   # Drive: size = space their old versions use
    trash: int = 0                                           # Drive: space used by the trash
    quota: dict | None = None                                # Drive: {"limit", "usage"}
    errors: int = 0
    scanned_at: float = field(default_factory=time.time)

    def safe(self) -> list[Item]:
        """What can go without losing anything: junk, and extra copies of duplicates."""
        extra = [it for g in self.duplicates for it in g.items[1:]]
        seen, out = set(), []
        for it in self.junk + extra:
            if it.key not in seen:
                seen.add(it.key)
                out.append(it)
        return out

    def safe_bytes(self) -> int:
        return sum(it.size for it in self.safe())


def _keep_first(items: list[Item]) -> list[Item]:
    """Which copy to keep: the oldest (likely the original), then the shortest path."""
    return sorted(items, key=lambda it: (it.modified or float("inf"), len(it.path), it.path))


# ---------------------------------------------------------------------------------- this PC
def _quick_hash(path: str, size: int) -> str:
    """The first 64 KB: one short read tells most different same-size files apart (only files that
    match here are read in full). One read per file keeps it quick on hard disks."""
    with open(path, "rb") as f:
        return hashlib.md5(f.read(65536), usedforsecurity=False).hexdigest()


def _full_hash(path: str) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_pc(folders: list[str], progress: Callable[[str], None] | None = None,
            should_stop: Callable[[], bool] | None = None) -> Report:
    """Scan PC folders. progress(text) is called now and then; should_stop() cancels (returns what's found)."""
    report = Report("pc", ", ".join(folders))
    files: list[Item] = []
    dir_sizes: dict[str, int] = defaultdict(int)
    last = 0.0

    def tell(text):
        nonlocal last
        if progress and time.monotonic() - last > 0.2:
            last = time.monotonic()
            progress(text)

    for root in folders:
        for dirpath, dirnames, filenames in os.walk(root):
            if should_stop and should_stop():
                break
            for d in list(dirnames):
                if d in JUNK_DIRS:          # a whole junk folder: one row, not every file in it
                    p = os.path.join(dirpath, d)
                    size = _tree_size(p)
                    report.junk.append(Item(p, p, size, is_dir=True))
                    dirnames.remove(d)
                    _add_up(dir_sizes, dirpath, root, size)
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    st = os.stat(p)
                except OSError:
                    report.errors += 1
                    continue
                it = Item(p, p, st.st_size, st.st_mtime)
                files.append(it)
                _add_up(dir_sizes, dirpath, root, st.st_size)
                if is_junk(name):
                    report.junk.append(it)
            tell(f"Looking at {dirpath}")
    report.files = len(files)
    report.total = sum(it.size for it in files) + sum(it.size for it in report.junk if it.is_dir)
    report.biggest_files = sorted(files, key=lambda it: -it.size)[:TOP]
    report.biggest_folders = [Item(p, p, s, is_dir=True)
                              for p, s in sorted(dir_sizes.items(), key=lambda kv: -kv[1])[:TOP]]

    # Duplicates: same size -> same start/middle/end -> same full checksum.
    by_size: dict[int, list[Item]] = defaultdict(list)
    for it in files:
        if it.size >= MIN_DUPLICATE and not is_junk(os.path.basename(it.path)):
            by_size[it.size].append(it)
    candidates = [g for g in by_size.values() if len(g) > 1]
    for n, group in enumerate(candidates, 1):
        if should_stop and should_stop():
            break
        tell(f"Comparing possible duplicates ({n} of {len(candidates)})")
        for quick in _group(group, lambda it: _quick_hash(it.path, it.size), report):
            for same in _group(quick, lambda it: _full_hash(it.path), report):
                report.duplicates.append(DuplicateGroup(same[0].size, _keep_first(same)))
    report.duplicates.sort(key=lambda g: -g.wasted)
    return report


def _group(items: list[Item], key, report: Report) -> list[list[Item]]:
    groups: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        try:
            groups[key(it)].append(it)
        except OSError:
            report.errors += 1
    return [g for g in groups.values() if len(g) > 1]


def _add_up(dir_sizes: dict[str, int], dirpath: str, root: str, size: int) -> None:
    """Count a file's size in its folder and every folder above it, up to the scanned folder."""
    d = dirpath
    while True:
        dir_sizes[d] += size
        if os.path.normcase(d) == os.path.normcase(root):
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent


def _tree_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


# ----------------------------------------------------------------------------- Google Drive
DRIVE_FIELDS = "nextPageToken, files(id, name, mimeType, size, quotaBytesUsed, md5Checksum, parents, modifiedTime)"


def _drive_time(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return 0.0


def list_owned(service, trashed: bool, progress: Callable[[str], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> list[dict]:
    """Every file the account owns in My Drive (only owned files use its storage)."""
    out, token = [], None
    while True:
        if should_stop and should_stop():
            break
        resp = service.files().list(q=f"'me' in owners and trashed = {'true' if trashed else 'false'}",
                                    fields=DRIVE_FIELDS, pageSize=1000, pageToken=token,
                                    spaces="drive").execute(num_retries=5)
        out += resp.get("files", [])
        if progress:
            progress(f"Listing Google Drive{' trash' if trashed else ''}… {len(out):,} items")
        token = resp.get("nextPageToken")
        if not token:
            return out
    return out


def build_drive_report(email: str, items: list[dict], trashed: list[dict], quota: dict | None) -> Report:
    """Work out the report from Drive's file list (no further requests)."""
    report = Report("drive", email, quota=quota)
    folders = {i["id"]: i for i in items if i["mimeType"] == FOLDER_MIME}
    files = [i for i in items if i["mimeType"] != FOLDER_MIME]
    path_cache: dict[str, str] = {}

    def folder_path(fid: str | None, depth: int = 0) -> str:
        if not fid or fid not in folders or depth > 50:
            return "My Drive"
        if fid not in path_cache:
            f = folders[fid]
            path_cache[fid] = f"{folder_path((f.get('parents') or [None])[0], depth + 1)}/{f['name']}"
        return path_cache[fid]

    dir_sizes: dict[str, int] = defaultdict(int)
    found: list[Item] = []
    for f in files:
        used = int(f.get("quotaBytesUsed") or 0)
        size = int(f.get("size") or 0)
        parent = (f.get("parents") or [None])[0]
        path = f"{folder_path(parent)}/{f['name']}"
        it = Item(f["id"], path, size, _drive_time(f.get("modifiedTime")),
                  extra={"md5": f.get("md5Checksum"), "used": used, "parent": parent})
        found.append(it)
        # Count the space the file really uses (with its old versions) in every folder above it.
        fid, depth = parent, 0
        while fid in folders and depth < 50:
            dir_sizes[fid] += used
            fid, depth = (folders[fid].get("parents") or [None])[0], depth + 1
        if is_junk(f["name"]) or "/__MACOSX/" in path + "/":
            report.junk.append(it)
        if used - size >= OLD_VERSIONS_MIN and f.get("md5Checksum"):
            report.old_versions.append(Item(f["id"], path, used - size, it.modified,
                                            extra={"current": size}))
    report.files = len(found)
    report.total = sum(it.extra["used"] for it in found)
    report.biggest_files = sorted(found, key=lambda it: -it.extra["used"])[:TOP]
    report.biggest_folders = [Item(fid, folder_path(fid), s, is_dir=True)
                              for fid, s in sorted(dir_sizes.items(), key=lambda kv: -kv[1])[:TOP]]
    for fid in list(folders):          # a __MACOSX folder: one junk row for the whole thing
        if folders[fid]["name"] in JUNK_DIRS:
            report.junk = [j for j in report.junk if not j.path.startswith(folder_path(fid) + "/")]
            report.junk.append(Item(fid, folder_path(fid), dir_sizes.get(fid, 0), is_dir=True))
    by_md5: dict[str, list[Item]] = defaultdict(list)
    junk_keys = {j.key for j in report.junk}
    for it in found:
        if it.extra["md5"] and it.size >= MIN_DUPLICATE and it.key not in junk_keys:
            by_md5[it.extra["md5"]].append(it)
    report.duplicates = sorted((DuplicateGroup(g[0].size, _keep_first(g)) for g in by_md5.values() if len(g) > 1),
                               key=lambda g: -g.wasted)
    report.old_versions.sort(key=lambda it: -it.size)
    report.trash = sum(int(t.get("quotaBytesUsed") or t.get("size") or 0) for t in trashed)
    return report


def scan_drive(service, email: str, progress=None, should_stop=None) -> Report:
    try:
        q = service.about().get(fields="storageQuota(limit, usage)").execute(num_retries=5)["storageQuota"]
        quota = {"limit": int(q["limit"]) if q.get("limit") else None, "usage": int(q.get("usage") or 0)}
    except Exception as e:  # noqa: BLE001 - the rest of the report still works
        log.debug("Couldn't read storage numbers: %s", e)
        quota = None
    items = list_owned(service, False, progress, should_stop)
    trashed = list_owned(service, True, progress, should_stop)
    if progress:
        progress("Working out what uses the space…")
    return build_drive_report(email, items, trashed, quota)


def delete_old_versions(service, file_id: str) -> int:
    """Remove a Drive file's old versions for good (the current one stays). Returns how many."""
    revs = service.revisions().list(fileId=file_id, fields="revisions(id, keepForever)").execute(num_retries=5)
    old = revs.get("revisions", [])[:-1]   # the last one is the current version
    n = 0
    for r in old:
        service.revisions().delete(fileId=file_id, revisionId=r["id"]).execute(num_retries=5)
        n += 1
    return n
