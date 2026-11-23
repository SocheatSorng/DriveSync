"""The Space tab without the window: scanning, what's selected, moving items to the Recycle Bin /
Drive trash, removing old Drive versions, and every text the tab shows.

Shared by the CustomTkinter view (space_view.py) and the Qt one (qtui/space_tab.py), so the code that
deletes files exists once and is tested once (test_space_model.py). The run_* functions are the bodies
of background threads: they only change the SpaceState and post ("space",) / ("space_progress",) to
app.ui; the window redraws from the state on its own thread.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import space
from progress import human_size

SYNCED = "This PC - synced folders"
PICK = "This PC - choose a folder…"
DRIVE = "Google Drive - "
ROWS = 100       # rows shown per list (the biggest first)
GROUPS = 30      # duplicate groups shown
INTRO = ("Find what's taking up space on this PC or in Google Drive: duplicates, junk files, the biggest "
         "folders and files, and old versions.")
NO_SYNCED_FOLDERS = "No synced folders yet - choose a folder instead."
SAFE_NOTE = ("Junk files (Mac leftovers, thumbnails, unfinished downloads) and extra copies of duplicate files. "
             "One copy of each duplicate is always kept.")
DUP_NOTE = "Files with exactly the same content. The oldest copy is marked Keep."
OLD_NOTE = ("Google Drive keeps earlier versions of files you changed, and they use your storage. Removing them "
            "keeps the current version. This can't be undone (Google doesn't put old versions in the trash).")
DRIVE_TRASH_URL = "https://drive.google.com/drive/trash"


def short(path: str, n: int = 80) -> str:
    return path if len(path) <= n else "…" + path[-(n - 1):]


class SpaceState:
    """Kept on the app (the window's widgets are thrown away when it's hidden)."""

    def __init__(self):
        self.report: space.Report | None = None
        self.selected: set[str] = set()         # items to move to the Recycle Bin / Drive trash
        self.old_selected: set[str] = set()     # Drive files whose old versions to remove
        self.status = ""
        self.progress = ""                      # short text for the busy button, e.g. "Moved 1,200 of 5,504…"
        self.busy = False
        self.stop = threading.Event()
        self.email: str | None = None           # account of a Drive report


def state_of(app) -> SpaceState:
    if not isinstance(getattr(app, "space", None), SpaceState):
        app.space = SpaceState()
    return app.space


def bin_name(where: str) -> str:
    return "Recycle Bin" if where == "pc" else "Drive trash"


# --------------------------------------------------------------------------- scanning
def sources(accounts_info: list[dict]) -> list[str]:
    """The "what to look at" menu: synced folders, a folder to pick, each signed-in Drive."""
    return [SYNCED, PICK, *[DRIVE + a["email"] for a in accounts_info if not a["needs_signin"]]]


def scan_target(choice: str, cfg: dict, picked: str | None = None):
    """("pc", [folders]) or ("drive", email); None = nothing to scan (PICK without a folder, or no
    synced folders - see NO_SYNCED_FOLDERS)."""
    if choice == PICK:
        return ("pc", [os.path.normpath(picked)]) if picked else None
    if choice == SYNCED:
        folders = [f["local"] for f in cfg.get("folders", []) if os.path.isdir(f["local"])]
        return ("pc", folders) if folders else None
    return "drive", choice[len(DRIVE):]


def start_scan(st: SpaceState) -> None:
    st.busy, st.report, st.status = True, None, "Starting…"
    st.stop.clear()
    st.selected.clear()
    st.old_selected.clear()


def run_scan(app, st: SpaceState, target) -> None:
    """Background thread: scan, then select what's safe to delete."""
    def tell(text):
        st.status = text
        app.ui.put(("space",))
    try:
        if target[0] == "pc":
            report = space.scan_pc(target[1], tell, st.stop.is_set)
            st.email = None
        else:
            st.email = target[1]
            drive = app.drive_for_browsing(target[1])
            report = space.scan_drive(drive.s, target[1], tell, st.stop.is_set)
        st.report = report
        st.selected = {it.key for it in report.safe()}
        space.log.info("Space: scanned %s - %d files, %s, %s safe to delete", report.title, report.files,
                       human_size(report.total), human_size(report.safe_bytes()))
        st.status = "Stopped early - showing what was found so far." if st.stop.is_set() else ""
    except Exception as e:  # noqa: BLE001
        space.log.exception("Space: scan failed")
        st.status = f"Couldn't finish: {e}"
    st.busy = False
    app.ui.put(("space",))


# --------------------------------------------------------------------------- what's shown
@dataclass(frozen=True)
class Summary:
    title: str
    text: str
    trash_button: str | None    # Drive: "Drive's trash uses 1.2 GB - open it"
    note: str


def summary(r: space.Report) -> Summary:
    lines = [f"{r.files:,} files · {human_size(r.total)}" + (f" · {r.errors} couldn't be read" if r.errors else "")]
    if r.quota and r.quota.get("limit"):
        q = r.quota
        lines.append(f"Google Drive: {human_size(q['usage'])} of {human_size(q['limit'])} used "
                     f"({100 * q['usage'] // q['limit']}%) - this includes Gmail and Google Photos.")
    lines.append(f"Safe to delete: {human_size(r.safe_bytes())} (junk files and extra copies).")
    synced_note = ("Files in a synced folder are removed on the other side too at the next sync (to the "
                   f"{'Drive trash' if r.where == 'pc' else 'Recycle Bin'}).")
    trash = (f"Drive's trash uses {human_size(r.trash)} - open it" if r.where == "drive" and r.trash else None)
    return Summary(r.title if r.where == "drive" else "This PC", "\n".join(lines), trash,
                   f"Selected items go to the {bin_name(r.where)}, so you can get them back. {synced_note}")


def item_size(r: space.Report, it: space.Item) -> int:
    return it.extra.get("used", it.size) if r.where == "drive" else it.size


def sizes(r: space.Report) -> dict[str, int]:
    """Every item that can be selected for the Recycle Bin / Drive trash, and its size."""
    items = [*r.safe(), *r.biggest_files, *(i for g in r.duplicates for i in g.items)]
    return {it.key: item_size(r, it) for it in items}


def selection(st: SpaceState) -> tuple[int, int]:
    s = sizes(st.report)
    keys = st.selected & set(s)
    return len(keys), sum(s[k] for k in keys)


def delete_button(st: SpaceState) -> tuple[str, bool]:
    """(text, enabled) of the "Move N selected to the Recycle Bin" button."""
    if st.busy:   # a move is running: say so on the button, and don't let it start a second one
        return st.progress or "Working…", False
    n, size = selection(st)
    if not n:
        return "Nothing selected", False
    return f"Move {n:,} selected ({human_size(size)}) to {bin_name(st.report.where)}", True


def safe_rows(r: space.Report) -> list[tuple[space.Item, str]]:
    dup_of = {i.key: g.items[0] for g in r.duplicates for i in g.items[1:]}
    rows = []
    for it in sorted(r.safe(), key=lambda i: -i.size)[:ROWS]:
        why = f"copy of {short(dup_of[it.key].path, 40)}" if it.key in dup_of else "junk"
        rows.append((it, f"{human_size(it.size):>9}   {short(it.path, 70)}   ({why})"))
    return rows


def more_text(n: int, shown: int = ROWS, what: str = "items") -> str | None:
    return f"…and {n - shown:,} more {what} (the biggest are shown)" if n > shown else None


def dup_title(r: space.Report) -> str:
    return f"Duplicates - {human_size(sum(g.wasted for g in r.duplicates))} in extra copies"


def dup_group_title(g) -> str:
    return f"{len(g.items)} copies · {human_size(g.size)} each"


def dup_row(i: int, it: space.Item) -> str:
    return ("Keep   " if i == 0 else "") + short(it.path, 90)


def old_title(r: space.Report) -> str:
    return f"Old versions - {human_size(sum(it.size for it in r.old_versions))}"


def old_row(it: space.Item) -> str:
    return f"{human_size(it.size):>9}   {short(it.path, 80)}"


def old_button(st: SpaceState) -> tuple[str, bool]:
    n = len(st.old_selected)
    text = f"Remove old versions of {n} file{'s' if n != 1 else ''}" if n else "Select files to remove old versions"
    return text, bool(n) and not st.busy


def biggest_row(r: space.Report, it: space.Item, select: bool) -> str:
    return f"{human_size(item_size(r, it)):>9}   {short(it.path, 80 if select else 85)}"


def open_target(r: space.Report, it: space.Item):
    """("folder", path) to open in Explorer (None if it's gone), or ("url", Drive page)."""
    if r.where == "pc":
        target = it.path if it.is_dir else os.path.dirname(it.path)
        return "folder", target if os.path.exists(target) else None
    fid = it.key if it.is_dir else it.extra.get("parent")
    return "url", f"https://drive.google.com/drive/folders/{fid}" if fid else "https://drive.google.com/drive/my-drive"


# --------------------------------------------------------------------------- deleting
def delete_question(st: SpaceState) -> tuple[str, bool]:
    """(question, warning) before moving the selection; warning when every copy of a duplicate is
    selected, so none of them would be kept."""
    r = st.report
    n, size = selection(st)
    name = bin_name(r.where)
    whole = sum(1 for g in r.duplicates if all(i.key in st.selected for i in g.items))
    warn = (f"\n\nAll copies of {whole} duplicate file(s) are selected, so none of them would be kept."
            if whole else "")
    return (f"Move {n:,} item(s) ({human_size(size)}) to the {name}?\n\n"
            f"You can restore them from the {name}.{warn}"), bool(whole)


def start_delete(st: SpaceState) -> set[str]:
    """Mark the state busy; returns the keys to move (give them to run_delete on a thread)."""
    keys = set(st.selected) & set(sizes(st.report))
    st.busy, st.progress = True, f"Moving 0 of {len(keys):,}…"
    st.status = f"Moving {len(keys):,} item(s) to the {bin_name(st.report.where)}… This can take a few minutes."
    return keys


def run_delete(app, st: SpaceState, keys: set[str]) -> None:
    """Background thread: move items to the Recycle Bin (this PC) or the Drive trash - never deleted
    outright - then take them out of the report."""
    r = st.report
    done, failed = set(), 0
    name = bin_name(r.where)
    last = 0.0

    def progress(i):
        nonlocal last
        now = time.monotonic()
        if now - last >= 0.3 or i == len(keys):
            last = now
            st.progress = f"Moving {i:,} of {len(keys):,}…"
            st.status = (f"Moving {len(keys):,} item(s) to the {name}: {i:,} done"
                         + (f", {failed:,} couldn't be moved" if failed else "") + "…")
            app.ui.put(("space_progress",))
    try:
        if r.where == "pc":
            from send2trash import send2trash
            for i, k in enumerate(keys, 1):
                if not os.path.lexists(k):   # already gone (moved or deleted elsewhere): nothing to do
                    done.add(k)
                    space.log.info("Space: already gone      %s", k)
                else:
                    try:
                        send2trash(k)
                        done.add(k)
                        space.log.info("Space: to Recycle Bin   %s", k)
                    except Exception as e:  # noqa: BLE001 - e.g. open in another app
                        failed += 1
                        space.log.warning("Space: couldn't move %s to the Recycle Bin: %s", k, e)
                progress(i)
        else:
            drive = app.drive_for_browsing(st.email)
            found = [*r.junk, *r.biggest_files, *(i for g in r.duplicates for i in g.items)]
            names = {it.key: it.path for it in found}
            for i, k in enumerate(keys, 1):
                try:
                    drive.trash(k)
                    done.add(k)
                    space.log.info("Space: to Drive trash   %s (%s)", names.get(k, ""), k)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    space.log.warning("Space: couldn't move %s to the Drive trash: %s", names.get(k, k), e)
                progress(i)
    except Exception as e:  # noqa: BLE001
        failed += len(keys) - len(done)
        space.log.error("Clearing space failed: %s", e)
    freed = forget(r, done)
    st.selected -= done
    st.status = (f"Moved {len(done):,} item(s) ({human_size(freed)}) to the {name}."
                 + (f" {failed} couldn't be moved (open in another app, or already gone)." if failed else ""))
    st.busy, st.progress = False, ""
    app.ui.put(("space",))


def old_question(st: SpaceState) -> tuple[list[space.Item], str]:
    items = [it for it in st.report.old_versions if it.key in st.old_selected]
    size = sum(it.size for it in items)
    return items, (f"Remove the old versions of {len(items)} file(s)? This frees about {human_size(size)}.\n\n"
                   "The current version of each file stays. Old versions can't be restored afterwards.")


def start_old(st: SpaceState) -> None:
    st.busy, st.status = True, "Removing old versions…"


def run_old(app, st: SpaceState, items: list[space.Item]) -> None:
    """Background thread: remove old versions of these Drive files (the current version stays)."""
    r = st.report
    done, failed = [], 0
    try:
        drive = app.drive_for_browsing(st.email)
        for it in items:
            try:
                n = space.delete_old_versions(drive.s, it.key)
                done.append(it)
                space.log.info("Space: removed %d old version(s) of %s", n, it.path)
            except Exception as e:  # noqa: BLE001
                space.log.warning("Couldn't remove old versions of %s: %s", it.path, e)
                failed += 1
    except Exception as e:  # noqa: BLE001
        failed = len(items)
        space.log.error("Removing old versions failed: %s", e)
    ids = {it.key for it in done}
    r.old_versions = [it for it in r.old_versions if it.key not in ids]
    st.old_selected -= ids
    st.status = (f"Removed old versions of {len(done)} file(s), about {human_size(sum(i.size for i in done))}."
                 + (f" {failed} couldn't be changed." if failed else ""))
    st.busy = False
    app.ui.put(("space",))


def forget(r: space.Report, keys: set[str]) -> int:
    """Take deleted items out of the report; returns the space they used."""
    if not keys:
        return 0
    freed = 0
    seen = set()
    for it in [*r.junk, *r.biggest_files, *(i for g in r.duplicates for i in g.items)]:
        if it.key in keys and it.key not in seen:
            seen.add(it.key)
            freed += item_size(r, it)
    r.junk = [it for it in r.junk if it.key not in keys]
    r.biggest_files = [it for it in r.biggest_files if it.key not in keys]
    groups = []
    for g in r.duplicates:
        g.items = [it for it in g.items if it.key not in keys]
        if len(g.items) > 1:
            groups.append(g)
    r.duplicates = groups
    r.total -= freed
    return freed
