"""The Space tab (CustomTkinter): what's using space on this PC or on Google Drive, and clearing it
out safely. The logic, the texts and the code that moves files are in space_model.py (shared with the
Qt window); this file only draws them."""
from __future__ import annotations

import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

import space
import space_model as sm
from space_model import DRIVE, GROUPS, PICK, ROWS, SYNCED, SpaceState, short  # noqa: F401 - (old names)

MUTED = ("#5f6368", "#9aa0a6")
CARD = ("#f1f3f4", "#2b2b2b")
RED, GREEN = "#d93025", "#1e8e3e"
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


class SpaceView:
    def __init__(self, parent, app):
        self.app = app
        self.st: SpaceState = sm.state_of(app)
        self.vars: dict[str, ctk.BooleanVar] = {}

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(4, 2))
        self.source = ctk.CTkOptionMenu(bar, values=sm.sources(app.accounts_info()), width=300,
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
        picked = None
        if choice == sm.PICK:
            picked = filedialog.askdirectory(parent=self.list.winfo_toplevel(), title="Folder to look at")
            if not picked:
                return
        target = sm.scan_target(choice, self.app.cfg, picked)
        if target is None:
            messagebox.showinfo("Drive Sync", sm.NO_SYNCED_FOLDERS, parent=self.list.winfo_toplevel())
            return
        sm.start_scan(self.st)
        self.render()
        threading.Thread(target=sm.run_scan, args=(self.app, self.st, target), daemon=True,
                         name="space-scan").start()

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
            self.status.configure(text=st.status or sm.INTRO)
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
        s = sm.summary(r)
        c = self.card(s.title, s.text)
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(4, 14))
        self.delete_btn = ctk.CTkButton(row, text="", width=280, command=self.delete_selected)
        self.delete_btn.pack(side="left")
        self._refresh_button()
        if s.trash_button:
            ctk.CTkButton(row, text=s.trash_button, width=230,
                          command=lambda: self.app.open_url(sm.DRIVE_TRASH_URL, self.st.email),
                          **SECONDARY).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(c, text=s.note, font=font(11), text_color=MUTED, anchor="w", justify="left",
                     wraplength=640).pack(anchor="w", padx=16, pady=(0, 12))

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
        text, enabled = sm.delete_button(self.st)
        self.delete_btn.configure(text=text, state="normal" if enabled else "disabled")

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
        from progress import human_size
        c = self.card(f"Safe to delete - {human_size(r.safe_bytes())}", sm.SAFE_NOTE)
        for it, label in sm.safe_rows(r):
            self._check(c, it, label, self.st.selected)
        self._more(c, len(items))

    def _duplicates(self, r: space.Report):
        if not r.duplicates:
            return
        c = self.card(sm.dup_title(r), sm.DUP_NOTE)
        for g in r.duplicates[:GROUPS]:
            ctk.CTkLabel(c, text=sm.dup_group_title(g), font=font(12, "bold"),
                         anchor="w").pack(anchor="w", padx=16, pady=(8, 0))
            for i, it in enumerate(g.items):
                self._check(c, it, sm.dup_row(i, it), self.st.selected)
        self._more(c, len(r.duplicates), GROUPS, "groups")

    def _old_versions(self, r: space.Report):
        if not r.old_versions:
            return
        c = self.card(sm.old_title(r), sm.OLD_NOTE)
        for it in r.old_versions[:ROWS]:
            self._check(c, it, sm.old_row(it), self.st.old_selected)
        text, enabled = sm.old_button(self.st)
        self.old_btn = ctk.CTkButton(c, text=text, width=280, fg_color=RED, hover_color="#b3261e",
                                     state="normal" if enabled else "disabled", command=self.delete_old_versions)
        self.old_btn.pack(anchor="w", padx=16, pady=(8, 14))

    def _refresh_old_button(self):
        text, enabled = sm.old_button(self.st)
        self.old_btn.configure(text=text, state="normal" if enabled else "disabled")

    def _biggest(self, title, items, r: space.Report, select: bool):
        if not items:
            return
        c = self.card(title)
        for it in items:
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=0, pady=0)
            text = sm.biggest_row(r, it, select)
            if select:
                self._check(row, it, text, self.st.selected).pack(side="left")
            else:
                ctk.CTkLabel(row, text=text, font=font(12), anchor="w").pack(side="left", padx=16)
            ctk.CTkButton(row, text="Open", width=56, height=22, command=lambda i=it: self.open_item(i),
                          **SECONDARY).pack(side="right", padx=12)
        ctk.CTkFrame(c, fg_color="transparent", height=8).pack()

    def _more(self, card, n, shown=ROWS, what="items"):
        text = sm.more_text(n, shown, what)
        if text:
            ctk.CTkLabel(card, text=text, font=font(11), text_color=MUTED).pack(anchor="w", padx=16)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    # ------------------------------------------------------------------ actions
    def open_item(self, it: space.Item):
        kind, target = sm.open_target(self.st.report, it)
        if kind == "folder":
            if target:
                os.startfile(target)  # noqa: S606 - a folder the scan found on this PC
        else:
            self.app.open_url(target, self.st.email)

    def delete_selected(self):
        st = self.st
        if st.busy or st.report is None:
            return
        question, warn = sm.delete_question(st)
        if not messagebox.askyesno("Drive Sync", question, icon="warning" if warn else "question",
                                   parent=self.list.winfo_toplevel()):
            return
        keys = sm.start_delete(st)
        self.render()
        threading.Thread(target=sm.run_delete, args=(self.app, st, keys), daemon=True,
                         name="space-delete").start()

    def delete_old_versions(self):
        st = self.st
        if st.busy or st.report is None:
            return
        items, question = sm.old_question(st)
        if not messagebox.askyesno("Drive Sync", question, icon="warning", parent=self.list.winfo_toplevel()):
            return
        sm.start_old(st)
        self.render()
        threading.Thread(target=sm.run_old, args=(self.app, st, items), daemon=True).start()
