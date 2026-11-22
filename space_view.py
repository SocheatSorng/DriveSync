"""The Space tab: what's using space on this PC or on Google Drive, and clearing it out safely."""
from __future__ import annotations

import os
import threading
import time
from tkinter import filedialog, messagebox

import customtkinter as ctk

import space
from progress import human_size

MUTED = ("#5f6368", "#9aa0a6")
CARD = ("#f1f3f4", "#2b2b2b")
RED, GREEN = "#d93025", "#1e8e3e"
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}
SYNCED = "This PC - synced folders"
PICK = "This PC - choose a folder…"
DRIVE = "Google Drive - "
ROWS = 100       # rows shown per list (the biggest first)
GROUPS = 30      # duplicate groups shown


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


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


class SpaceView:
    def __init__(self, parent, app):
        self.app = app
        if not hasattr(app, "space"):
            app.space = SpaceState()
        self.st: SpaceState = app.space
        self.vars: dict[str, ctk.BooleanVar] = {}

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(4, 2))
        emails = [a["email"] for a in app.accounts_info() if not a["needs_signin"]]
        self.source = ctk.CTkOptionMenu(bar, values=[SYNCED, PICK, *[DRIVE + e for e in emails]], width=300,
                                        dynamic_resizing=False)
        self.source.pack(side="left")
        self.scan_btn = ctk.CTkButton(bar, text="Scan", width=80, command=self.scan)
        self.scan_btn.pack(side="left", padx=8)
        self.stop_btn = ctk.CTkButton(bar, text="Stop", width=64, command=self.st.stop.set, **SECONDARY)
        self.status = ctk.CTkLabel(parent, text="", font=font(12), text_color=MUTED, anchor="w", justify="left")
        self.status.pack(fill="x", padx=8, pady=(2, 4))
        self.list = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.list.pack(fill="both", expand=True)
        self.render()

    # ------------------------------------------------------------------ scanning
    def scan(self):
        choice = self.source.get()
        if choice == PICK:
            folder = filedialog.askdirectory(parent=self.list.winfo_toplevel(), title="Folder to look at")
            if not folder:
                return
            target = ("pc", [os.path.normpath(folder)])
        elif choice == SYNCED:
            folders = [f["local"] for f in self.app.cfg.get("folders", []) if os.path.isdir(f["local"])]
            if not folders:
                messagebox.showinfo("Drive Sync", "No synced folders yet - choose a folder instead.",
                                    parent=self.list.winfo_toplevel())
                return
            target = ("pc", folders)
        else:
            target = ("drive", choice[len(DRIVE):])
        st = self.st
        st.busy, st.report, st.status = True, None, "Starting…"
        st.stop.clear()
        st.selected.clear()
        st.old_selected.clear()
        self.render()
        threading.Thread(target=self._scan_thread, args=(target,), daemon=True, name="space-scan").start()

    def _scan_thread(self, target):
        st, app = self.st, self.app

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

    # ------------------------------------------------------------------ drawing
    def render(self):
        """Redraw from self.st (called on the UI thread)."""
        st = self.st
        self.scan_btn.configure(state="disabled" if st.busy else "normal")
        if st.busy:
            self.stop_btn.pack(side="left")
        else:
            self.stop_btn.pack_forget()
        for w in self.list.winfo_children():
            w.destroy()
        self.vars.clear()
        r = st.report
        if r is None:
            self.status.configure(text=st.status or "Find what's taking up space on this PC or in Google Drive: "
                                  "duplicates, junk files, the biggest folders and files, and old versions.")
            return
        self.status.configure(text=st.status)
        self._summary(r)
        self._safe(r)
        self._duplicates(r)
        if r.where == "drive":
            self._old_versions(r)
        self._biggest("Biggest folders", r.biggest_folders, r, select=False)
        self._biggest("Biggest files", r.biggest_files, r, select=True)

    def card(self, title, note=""):
        c = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=10)
        c.pack(fill="x", padx=4, pady=(4, 10))
        ctk.CTkLabel(c, text=title, font=font(14, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        if note:
            ctk.CTkLabel(c, text=note, font=font(12), text_color=MUTED, anchor="w", justify="left",
                         wraplength=640).pack(anchor="w", padx=16, pady=(2, 4))
        return c

    def _summary(self, r: space.Report):
        bin_name = "Recycle Bin" if r.where == "pc" else "Drive trash"
        lines = [f"{r.files:,} files · {human_size(r.total)}"
                 + (f" · {r.errors} couldn't be read" if r.errors else "")]
        if r.quota and r.quota.get("limit"):
            q = r.quota
            lines.append(f"Google Drive: {human_size(q['usage'])} of {human_size(q['limit'])} used "
                         f"({100 * q['usage'] // q['limit']}%) - this includes Gmail and Google Photos.")
        lines.append(f"Safe to delete: {human_size(r.safe_bytes())} (junk files and extra copies).")
        c = self.card(r.title if r.where == "drive" else "This PC", "\n".join(lines))
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(4, 14))
        self.delete_btn = ctk.CTkButton(row, text="", width=280, command=self.delete_selected)
        self.delete_btn.pack(side="left")
        self._refresh_button()
        if r.where == "drive" and r.trash:
            ctk.CTkButton(row, text=f"Drive's trash uses {human_size(r.trash)} - open it", width=230,
                          command=lambda: self.app.open_url("https://drive.google.com/drive/trash", st_email(self)),
                          **SECONDARY).pack(side="left", padx=(8, 0))
        synced_note = ("Files in a synced folder are removed on the other side too at the next sync (to the "
                       f"{'Drive trash' if r.where == 'pc' else 'Recycle Bin'}).")
        ctk.CTkLabel(c, text=f"Selected items go to the {bin_name}, so you can get them back. {synced_note}",
                     font=font(11), text_color=MUTED, anchor="w", justify="left", wraplength=640).pack(
            anchor="w", padx=16, pady=(0, 12))

    @staticmethod
    def _delete_text(n, size, bin_name):
        return f"Move {n:,} selected ({human_size(size)}) to {bin_name}" if n else "Nothing selected"

    def _selection(self) -> tuple[int, int]:
        sizes = self._sizes()
        keys = self.st.selected & set(sizes)
        return len(keys), sum(sizes[k] for k in keys)

    def _check(self, parent, it: space.Item, label: str, selection: set[str]):
        var = self.vars.get(it.key) if selection is self.st.selected else None
        if var is None:
            var = ctk.BooleanVar(value=it.key in selection)
            if selection is self.st.selected:
                self.vars[it.key] = var

        def toggle():
            (selection.add if var.get() else selection.discard)(it.key)
            if selection is self.st.selected:
                self._refresh_button()
            else:
                self._refresh_old_button()
        cb = ctk.CTkCheckBox(parent, text=label, variable=var, command=toggle, font=font(12),
                             checkbox_width=18, checkbox_height=18,
                             state="disabled" if self.st.busy else "normal")
        cb.pack(anchor="w", padx=16, pady=1)
        return cb

    def _refresh_button(self):
        st, r = self.st, self.st.report
        if st.busy:   # a move is running: say so on the button, and don't let it start a second one
            self.delete_btn.configure(text=st.progress or "Working…", state="disabled")
            return
        n, size = self._selection()
        self.delete_btn.configure(text=self._delete_text(n, size, "Recycle Bin" if r.where == "pc" else "Drive trash"),
                                  state="normal" if n else "disabled")

    def show_progress(self):
        """Cheap update while moving files (redrawing every list would be slow)."""
        self.status.configure(text=self.st.status)
        if self.st.report is not None and getattr(self, "delete_btn", None) is not None:
            try:
                self._refresh_button()
            except Exception:  # noqa: BLE001 - the button was just destroyed by a redraw
                pass

    def _safe(self, r: space.Report):
        items = r.safe()
        if not items:
            return
        c = self.card(f"Safe to delete - {human_size(r.safe_bytes())}",
                      "Junk files (Mac leftovers, thumbnails, unfinished downloads) and extra copies of "
                      "duplicate files. One copy of each duplicate is always kept.")
        dup_of = {i.key: g.items[0] for g in r.duplicates for i in g.items[1:]}
        for it in sorted(items, key=lambda i: -i.size)[:ROWS]:
            why = f"copy of {short(dup_of[it.key].path, 40)}" if it.key in dup_of else "junk"
            self._check(c, it, f"{human_size(it.size):>9}   {short(it.path, 70)}   ({why})", self.st.selected)
        self._more(c, len(items))

    def _duplicates(self, r: space.Report):
        if not r.duplicates:
            return
        wasted = sum(g.wasted for g in r.duplicates)
        c = self.card(f"Duplicates - {human_size(wasted)} in extra copies",
                      "Files with exactly the same content. The oldest copy is marked Keep.")
        for g in r.duplicates[:GROUPS]:
            ctk.CTkLabel(c, text=f"{len(g.items)} copies · {human_size(g.size)} each", font=font(12, "bold"),
                         anchor="w").pack(anchor="w", padx=16, pady=(8, 0))
            for i, it in enumerate(g.items):
                self._check(c, it, ("Keep   " if i == 0 else "") + short(it.path, 90), self.st.selected)
        self._more(c, len(r.duplicates), GROUPS, "groups")

    def _old_versions(self, r: space.Report):
        if not r.old_versions:
            return
        total = sum(it.size for it in r.old_versions)
        c = self.card(f"Old versions - {human_size(total)}",
                      "Google Drive keeps earlier versions of files you changed, and they use your storage. "
                      "Removing them keeps the current version. This can't be undone (Google doesn't put old "
                      "versions in the trash).")
        for it in r.old_versions[:ROWS]:
            self._check(c, it, f"{human_size(it.size):>9}   {short(it.path, 80)}", self.st.old_selected)
        n = len(self.st.old_selected)
        self.old_btn = ctk.CTkButton(c, text=self._old_text(), width=280, fg_color=RED, hover_color="#b3261e",
                                     state="normal" if n and not self.st.busy else "disabled",
                                     command=self.delete_old_versions)
        self.old_btn.pack(anchor="w", padx=16, pady=(8, 14))

    def _old_text(self):
        n = len(self.st.old_selected)
        return f"Remove old versions of {n} file{'s' if n != 1 else ''}" if n else "Select files to remove old versions"

    def _refresh_old_button(self):
        self.old_btn.configure(text=self._old_text(),
                               state="normal" if self.st.old_selected and not self.st.busy else "disabled")

    def _biggest(self, title, items, r: space.Report, select: bool):
        if not items:
            return
        c = self.card(title)
        for it in items:
            size = it.extra.get("used", it.size) if r.where == "drive" else it.size
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=0, pady=0)
            if select:
                self._check(row, it, f"{human_size(size):>9}   {short(it.path, 80)}", self.st.selected).pack(
                    side="left")
            else:
                ctk.CTkLabel(row, text=f"{human_size(size):>9}   {short(it.path, 85)}", font=font(12),
                             anchor="w").pack(side="left", padx=16)
            ctk.CTkButton(row, text="Open", width=56, height=22, command=lambda i=it: self.open_item(i),
                          **SECONDARY).pack(side="right", padx=12)
        ctk.CTkFrame(c, fg_color="transparent", height=8).pack()

    def _more(self, card, n, shown=ROWS, what="items"):
        if n > shown:
            ctk.CTkLabel(card, text=f"…and {n - shown:,} more {what} (the biggest are shown)", font=font(11),
                         text_color=MUTED).pack(anchor="w", padx=16)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    # ------------------------------------------------------------------ actions
    def open_item(self, it: space.Item):
        r = self.st.report
        if r.where == "pc":
            target = it.path if it.is_dir else os.path.dirname(it.path)
            if os.path.exists(target):
                os.startfile(target)  # noqa: S606 - a folder the scan found on this PC
        else:
            fid = it.key if it.is_dir else it.extra.get("parent")
            url = f"https://drive.google.com/drive/folders/{fid}" if fid else "https://drive.google.com/drive/my-drive"
            self.app.open_url(url, self.st.email)

    def delete_selected(self):
        st, r = self.st, self.st.report
        if st.busy or r is None:
            return
        n, size = self._selection()
        bin_name = "Recycle Bin" if r.where == "pc" else "Drive trash"
        whole = sum(1 for g in r.duplicates if all(i.key in st.selected for i in g.items))
        warn = (f"\n\nAll copies of {whole} duplicate file(s) are selected, so none of them would be kept."
                if whole else "")
        if not messagebox.askyesno("Drive Sync", f"Move {n:,} item(s) ({human_size(size)}) to the {bin_name}?\n\n"
                                   f"You can restore them from the {bin_name}.{warn}",
                                   icon="warning" if whole else "question", parent=self.list.winfo_toplevel()):
            return
        keys = set(st.selected) & set(self._sizes())
        st.busy, st.progress = True, f"Moving 0 of {len(keys):,}…"
        st.status = f"Moving {len(keys):,} item(s) to the {bin_name}… This can take a few minutes."
        self.render()
        threading.Thread(target=self._delete_thread, args=(keys,), daemon=True, name="space-delete").start()

    def _sizes(self) -> dict[str, int]:
        r = self.st.report
        return {it.key: it.extra.get("used", it.size) if r.where == "drive" else it.size
                for it in [*r.safe(), *r.biggest_files, *(i for g in r.duplicates for i in g.items)]}

    def _delete_thread(self, keys: set[str]):
        st, r, app = self.st, self.st.report, self.app
        done, failed = set(), 0
        bin_name = "Recycle Bin" if r.where == "pc" else "Drive trash"
        last = 0.0

        def progress(i):
            nonlocal last
            now = time.monotonic()
            if now - last >= 0.3 or i == len(keys):
                last = now
                st.progress = f"Moving {i:,} of {len(keys):,}…"
                st.status = (f"Moving {len(keys):,} item(s) to the {bin_name}: {i:,} done"
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
        freed = _forget(r, done)
        st.selected -= done
        st.status = (f"Moved {len(done):,} item(s) ({human_size(freed)}) to the "
                     f"{'Recycle Bin' if r.where == 'pc' else 'Drive trash'}."
                     + (f" {failed} couldn't be moved (open in another app, or already gone)." if failed else ""))
        st.busy, st.progress = False, ""
        app.ui.put(("space",))

    def delete_old_versions(self):
        st, r = self.st, self.st.report
        if st.busy or r is None:
            return
        items = [it for it in r.old_versions if it.key in st.old_selected]
        size = sum(it.size for it in items)
        if not messagebox.askyesno("Drive Sync", f"Remove the old versions of {len(items)} file(s)? "
                                   f"This frees about {human_size(size)}.\n\nThe current version of each file "
                                   "stays. Old versions can't be restored afterwards.",
                                   icon="warning", parent=self.list.winfo_toplevel()):
            return
        st.busy, st.status = True, "Removing old versions…"
        self.render()
        threading.Thread(target=self._old_thread, args=(items,), daemon=True).start()

    def _old_thread(self, items):
        st, r, app = self.st, self.st.report, self.app
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


def st_email(view: SpaceView) -> str | None:
    return view.st.email


def _forget(r: space.Report, keys: set[str]) -> int:
    """Take deleted items out of the report; returns the space they used."""
    if not keys:
        return 0
    freed = 0
    seen = set()
    for it in [*r.junk, *r.biggest_files, *(i for g in r.duplicates for i in g.items)]:
        if it.key in keys and it.key not in seen:
            seen.add(it.key)
            freed += it.extra.get("used", it.size) if r.where == "drive" else it.size
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
