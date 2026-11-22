"""Main window for Drive Sync, built with CustomTkinter. Created and driven by tray_app.App."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import urlencode

import customtkinter as ctk

import browsers
from activity_view import ActivityView

STATE_COLORS = {"idle": "#1a73e8", "syncing": "#f9ab00", "error": "#d93025", "paused": "#80868b"}
MUTED = ("#5f6368", "#9aa0a6")          # (light, dark)
CARD = ("#f1f3f4", "#2b2b2b")
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("#1f1f1f", "#e3e3e3"),
             "border_color": ("#c4c7c5", "#5f6368"), "hover_color": ("#e8eaed", "#3c4043")}
DANGER = {"fg_color": "transparent", "text_color": ("#d93025", "#f28b82"),
          "hover_color": ("#fce8e6", "#3c2a2a")}
MODES = {"Two-way": "two_way", "Upload only (backup)": "upload"}
LIMITS = {"No limit": 0, "256 KB/s": 256, "512 KB/s": 512, "1 MB/s": 1024, "2 MB/s": 2048,
          "5 MB/s": 5120, "10 MB/s": 10240}
POLLS = {"Every 5 seconds": 5, "Every 10 seconds": 10, "Every 30 seconds": 30, "Every minute": 60}
INTERVALS = {"Every 5 minutes": 5, "Every 15 minutes": 15, "Every 30 minutes": 30, "Every hour": 60,
             "Every 3 hours": 180, "Every 6 hours": 360, "Once a day": 1440}


def autowrap(label, container, margin=0) -> None:
    """Wrap a label's text to its container's current width (instead of a fixed width that gets cut off).
    margin: real pixels to leave free (a number, or a function for space that changes, like buttons)."""
    def resize(event):
        if not label.winfo_exists():
            return
        scale = label._get_widget_scaling()  # CTk scales wraplength itself; event sizes are real pixels
        free = event.width - (margin() if callable(margin) else margin)
        width = max(120, int(free / scale))
        if label.cget("wraplength") != width:
            label.configure(wraplength=width)
    container.bind("<Configure>", resize, add="+")


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


# --------------------------------------------------------------------------- #
def _strip_my_drive(path: str) -> str:
    """'My Drive/School/2026' -> 'School/2026' (typed paths are always inside My Drive)."""
    path = path.strip().replace("\\", "/").strip("/")
    return path[len("My Drive"):].lstrip("/") if path.lower().startswith("my drive") else path


class FolderDialog(ctk.CTkToplevel):
    """Add / edit one synced folder. self.result is the folder dict, or None if cancelled."""

    def __init__(self, parent, app, title: str, data: dict | None = None):
        super().__init__(parent)
        self.app = app
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.after(250, lambda: app.apply_window_icon(self))
        self.result = None
        self.data = data or {}
        self.accounts = [a["email"] for a in app.accounts_info()]
        current = app.folder_account(self.data, self.accounts) if self.data else None
        if current and current not in self.accounts:
            self.accounts.append(current)  # keep showing a removed account rather than silently switching
        self.account = tk.StringVar(value=current or (self.accounts[0] if self.accounts else ""))
        self.local = tk.StringVar(value=self.data.get("local", ""))
        # A folder chosen in the Drive browser is stored by ID (+ its path, for display).
        self.drive_id: str | None = self.data.get("drive_folder_id")
        self.chosen_label: str | None = self.data.get("drive_folder") if self.drive_id else None
        shown = self.data.get("drive_folder", "")
        if shown and not self.drive_id:
            shown = f"My Drive/{_strip_my_drive(shown)}"
        self.drive = tk.StringVar(value=shown)
        self.delete = tk.BooleanVar(value=bool(self.data.get("delete")))
        self.exclude = tk.StringVar(value=", ".join(self.data.get("exclude", [])))
        self.account.trace_add("write", lambda *_: self._forget_choice())

        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(padx=24, pady=20, fill="both")
        f.columnconfigure(0, weight=1)

        def label(row, text, muted=False, pad=0):
            ctk.CTkLabel(f, text=text, font=font(12 if muted else 13, "normal" if muted else "bold"),
                         text_color=MUTED if muted else None, anchor="w", justify="left", wraplength=520).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(pad, 2))

        label(0, "Folder on this PC")
        ctk.CTkEntry(f, textvariable=self.local, width=420).grid(row=1, column=0, sticky="ew")
        ctk.CTkButton(f, text="Browse…", width=100, command=self.browse, **SECONDARY).grid(
            row=1, column=1, padx=(8, 0))

        label(2, "Google account", pad=16)
        if self.accounts:
            ctk.CTkOptionMenu(f, values=self.accounts, variable=self.account, dynamic_resizing=False).grid(
                row=3, column=0, columnspan=2, sticky="ew")
        else:
            ctk.CTkLabel(f, text="No account added yet. Add one in Settings → Google accounts; this folder "
                                 "will use it.", font=font(12), text_color=MUTED, anchor="w").grid(
                row=3, column=0, columnspan=2, sticky="w")

        label(4, "Google Drive folder", pad=16)
        ctk.CTkEntry(f, textvariable=self.drive).grid(row=5, column=0, sticky="ew")
        ctk.CTkButton(f, text="Choose…", width=100, command=self.choose_drive_folder).grid(
            row=5, column=1, padx=(8, 0))
        label(6, "Click Choose… to pick a folder in your Drive (My Drive, shared folders or shared drives) - "
                 "or type a path like My Drive/PC Backup/Photos to create one.", muted=True)

        label(7, "Skip files or folders matching", pad=16)
        ctk.CTkEntry(f, textvariable=self.exclude, placeholder_text="*.log, node_modules, .git").grid(
            row=8, column=0, columnspan=2, sticky="ew")
        label(9, "Comma-separated patterns.", muted=True)

        label(10, "Sync direction", pad=16)
        self.mode = ctk.CTkSegmentedButton(f, values=list(MODES), command=lambda _: self._show_mode())
        self.mode.set(next(k for k, v in MODES.items() if v == self.data.get("mode", "two_way")))
        self.mode.grid(row=11, column=0, columnspan=2, sticky="w")
        self.mode_help = ctk.CTkLabel(f, text="", font=font(12), text_color=MUTED, anchor="w", justify="left",
                                      wraplength=520)
        self.mode_help.grid(row=12, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.delete_box = ctk.CTkCheckBox(f, text="Make Drive match this PC exactly", variable=self.delete,
                                          font=font())
        self.delete_help = ctk.CTkLabel(
            f, text="Files in the Drive folder that aren't in the PC folder - including files that were already "
                    "there - are moved to the Drive trash (recoverable for 30 days). Leave this off to only add "
                    "and update files.", font=font(12), text_color=MUTED, anchor="w", justify="left", wraplength=520)
        self._show_mode()

        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.grid(row=15, column=0, columnspan=2, sticky="e", pady=(22, 0))
        ctk.CTkButton(btns, text="Cancel", width=90, command=self.destroy, **SECONDARY).pack(side="right")
        ctk.CTkButton(btns, text="Save", width=90, command=self.ok).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda _: self.ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._grab)
        if not self.local.get():
            self.after(300, self.browse)
        self.wait_window()

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _show_mode(self):
        if MODES[self.mode.get()] == "two_way":
            self.mode_help.configure(text="Changes on either side are copied to the other: new, edited and deleted "
                                          "files. Deleted files go to the Windows Recycle Bin or the Drive trash. "
                                          "If a file is edited on both sides, both versions are kept.")
            self.delete_box.grid_remove()
            self.delete_help.grid_remove()
        else:
            self.mode_help.configure(text="This PC is the master copy: files are only uploaded. Changes made on "
                                          "Drive never touch this PC.")
            self.delete_box.grid(row=13, column=0, columnspan=2, sticky="w", pady=(12, 0))
            self.delete_help.grid(row=14, column=0, columnspan=2, sticky="w")

    def _forget_choice(self):
        # A chosen folder belongs to one account; switching accounts means choosing again.
        if self.drive_id:
            self.drive_id, self.chosen_label = None, None
            self.drive.set("")

    def browse(self):
        path = filedialog.askdirectory(parent=self, title="Choose a folder to sync",
                                       initialdir=self.local.get() or os.path.expanduser("~"))
        if path:
            path = os.path.normpath(path)
            self.local.set(path)
            if not self.drive.get():
                self.drive.set(f"My Drive/PC Backup/{Path(path).name}")

    def choose_drive_folder(self):
        email = self.account.get()
        if not email:
            messagebox.showinfo("Drive Sync", "Add a Google account in Settings first.", parent=self)
            return
        picked = DriveFolderBrowser(self, self.app, email).result
        if picked:
            self.drive_id, self.chosen_label = picked
            self.drive.set(self.chosen_label)
        self._grab()

    def ok(self):
        local = self.local.get().strip()
        if not os.path.isdir(local):
            messagebox.showerror("Drive Sync", "Please choose a folder that exists on this PC.", parent=self)
            return
        local = os.path.normpath(local)
        text = self.drive.get().strip()
        account = self.account.get() or None
        mode = MODES[self.mode.get()]
        result = {**self.data, "local": local, "account": account, "mode": mode,
                  "delete": self.delete.get() if mode == "upload" else False,
                  "exclude": [x.strip() for x in self.exclude.get().split(",") if x.strip()]}
        result.pop("drive_folder_id", None)
        if self.drive_id and text == self.chosen_label:
            result["drive_folder_id"] = self.drive_id
            result["drive_folder"] = self.chosen_label
        else:
            result["drive_folder"] = _strip_my_drive(text) or f"PC Backup/{Path(local).name}"
        if account is None:
            result.pop("account")
        self.result = result
        self.destroy()


# --------------------------------------------------------------------------- #
class DriveFolderBrowser(ctk.CTkToplevel):
    """Browse an account's Google Drive and pick a folder. self.result = (folder_id, path) or None."""

    ROOTS = "__roots__"
    SHARED = "__shared__"
    DRIVES = "__drives__"

    def __init__(self, parent, app, email: str):
        super().__init__(parent)
        self.app = app
        self.email = email
        self.title("Choose a Google Drive folder")
        self.geometry("560x520")
        self.minsize(460, 400)
        self.transient(parent)
        self.after(250, lambda: app.apply_window_icon(self))
        self.result = None
        self.drive = None                    # created in a background thread
        self.lock = threading.Lock()         # one API call at a time (the client isn't thread-safe)
        self.results: queue.Queue = queue.Queue()
        # Navigation: list of (folder id, name, can add files here)
        self.stack: list[tuple[str, str, bool]] = [(self.ROOTS, "Google Drive", False), ("root", "My Drive", True)]

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        self.up_btn = ctk.CTkButton(top, text="← Back", width=80, command=self.go_up, **SECONDARY)
        self.up_btn.pack(side="left")
        self.path_lbl = ctk.CTkLabel(top, text="", font=font(13, "bold"), anchor="w")
        self.path_lbl.pack(side="left", padx=10, fill="x", expand=True)
        ctk.CTkLabel(self, text=email, font=font(12), text_color=MUTED, anchor="w").pack(fill="x", padx=18)

        # Pack the bottom bar first so the folder list can never push the buttons out of view.
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        self.new_btn = ctk.CTkButton(bottom, text="+ New folder", width=110, command=self.new_folder, **SECONDARY)
        self.new_btn.pack(side="left")
        ctk.CTkButton(bottom, text="Cancel", width=90, command=self.destroy, **SECONDARY).pack(side="right")
        self.select_btn = ctk.CTkButton(bottom, text="Use this folder", width=130, command=self.select)
        self.select_btn.pack(side="right", padx=(0, 8))
        self.note = ctk.CTkLabel(self, text="", font=font(12), text_color=MUTED)
        self.note.pack(side="bottom", fill="x", padx=18)

        self.list = ctk.CTkScrollableFrame(self, fg_color=CARD, corner_radius=10)
        self.list.pack(fill="both", expand=True, padx=16, pady=8)

        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        # Positions are in real screen pixels; sizes are DPI-scaled. Keep the window above the taskbar.
        scale = self._get_window_scaling()
        w, h = round(560 * scale), round(520 * scale)
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = min(parent.winfo_rooty(), self.winfo_screenheight() - h - round(80 * scale))
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._grab)
        self.after(100, self._poll)
        self.load()
        self.wait_window()

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    # -- background work -------------------------------------------------------
    def _run(self, fn, on_done):
        """Run fn(drive) in a thread; on_done(result_or_exception) runs on the UI thread."""
        def work():
            try:
                with self.lock:
                    if self.drive is None:
                        self.drive = self.app.drive_for_browsing(self.email)
                    out = fn(self.drive)
            except Exception as e:  # noqa: BLE001 - shown to the user
                out = e
            self.results.put((on_done, out))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                on_done, out = self.results.get_nowait()
                on_done(out)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    # -- navigation ------------------------------------------------------------
    @property
    def here(self) -> tuple[str, str, bool]:
        return self.stack[-1]

    def path_text(self) -> str:
        return "/".join(name for _, name, _ in self.stack[1:]) or "Google Drive"

    def load(self):
        folder_id, _, can_add = self.here
        self.path_lbl.configure(text=self.path_text())
        self.up_btn.configure(state="normal" if len(self.stack) > 1 else "disabled")
        virtual = folder_id in (self.ROOTS, self.SHARED, self.DRIVES)
        self.select_btn.configure(state="disabled" if virtual or not can_add else "normal")
        self.new_btn.configure(state="disabled" if virtual or not can_add else "normal")
        self.note.configure(text="" if virtual or can_add else "View-only folder - you can't add files here.")
        self._message("Loading…")

        if folder_id == self.ROOTS:
            self.show([("root", "My Drive", True), (self.SHARED, "Shared with me", False),
                       (self.DRIVES, "Shared drives", False)])
            return
        if folder_id == self.SHARED:
            fn = lambda d: [(f["id"], f["name"], f.get("capabilities", {}).get("canAddChildren", False))  # noqa: E731
                            for f in d.list_shared_with_me()]
        elif folder_id == self.DRIVES:
            fn = lambda d: [(x["id"], x["name"], True) for x in d.list_shared_drives()]  # noqa: E731
        else:
            fn = lambda d: [(f["id"], f["name"], f.get("capabilities", {}).get("canAddChildren", True))  # noqa: E731
                            for f in d.list_subfolders(folder_id)]
        expected = len(self.stack)
        self._run(fn, lambda out: self.show(out) if len(self.stack) == expected else None)

    def _message(self, text: str, color=MUTED):
        for w in self.list.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.list, text=text, font=font(), text_color=color, wraplength=460,
                     justify="left").pack(pady=24, padx=12)

    def show(self, out):
        if isinstance(out, Exception):
            from accounts import NeedsSignIn
            if isinstance(out, NeedsSignIn):
                self.app.needs_signin.add(self.email)
                self.app.ui.put(("account",))
                text = (f"{out}.\n\nClose this window, go to Settings → Google accounts and click "
                        "“Sign in again”, then try again.")
            else:
                text = f"Couldn't load this folder:\n{out}"
            self._message(text, "#d93025")
            return
        for w in self.list.winfo_children():
            w.destroy()
        if not out:
            self._message("No folders here." + ("" if self.here[0] in (self.ROOTS, self.SHARED, self.DRIVES)
                                                else " Click “Use this folder”, or create one with “+ New folder”."))
            return
        for fid, name, can_add in out:
            ctk.CTkButton(self.list, text=f"📁  {name}", anchor="w", height=32, font=font(),
                          fg_color="transparent", text_color=("#1f1f1f", "#e3e3e3"),
                          hover_color=("#e8eaed", "#3c4043"),
                          command=lambda f=(fid, name, can_add): self.open(f)).pack(fill="x", padx=4, pady=1)

    def open(self, folder: tuple[str, str, bool]):
        self.stack.append(folder)
        self.load()

    def go_up(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.load()

    def new_folder(self):
        name = ctk.CTkInputDialog(text="Name of the new folder:", title="New folder").get_input()
        self._grab()
        name = (name or "").strip()
        if not name:
            return
        parent_id = self.here[0]
        self._message("Creating folder…")

        def done(out):
            if isinstance(out, Exception):
                self._message(f"Couldn't create the folder:\n{out}", "#d93025")
            else:
                self.open((out, name, True))
        self._run(lambda d: d.create_folder(name, parent_id), done)

    def select(self):
        folder_id, _, _ = self.here
        self.result = (folder_id, self.path_text())
        self.destroy()


# --------------------------------------------------------------------------- #
class AddAccountDialog(ctk.CTkToplevel):
    """Asks which browser profile to sign in with. self.confirmed / self.profile_id."""

    def __init__(self, parent, app, choices: dict[str, str | None]):
        super().__init__(parent)
        self.title("Add Google account")
        self.resizable(False, False)
        self.transient(parent)
        self.after(250, lambda: app.apply_window_icon(self))
        self.confirmed = False
        self.profile_id = None
        self.choices = choices

        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(padx=24, pady=20, fill="both")
        ctk.CTkLabel(f, text="Sign in with", font=font(13, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(f, text="Pick the browser profile that's signed in to (or should hold) this Google account. "
                             "Drive Sync remembers it and opens this account's Drive links there too.",
                     font=font(12), text_color=MUTED, justify="left", wraplength=420, anchor="w").pack(
            anchor="w", pady=(2, 10))
        self.menu = ctk.CTkOptionMenu(f, values=list(choices), width=420, dynamic_resizing=False)
        self.menu.set(next(iter(choices)))
        self.menu.pack(anchor="w")
        ctk.CTkLabel(f, text="On Google's page, choose the account - or “Use another account”.",
                     font=font(12), text_color=MUTED, anchor="w").pack(anchor="w", pady=(10, 0))

        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.pack(anchor="e", pady=(20, 0))
        ctk.CTkButton(btns, text="Cancel", width=90, command=self.destroy, **SECONDARY).pack(side="right")
        ctk.CTkButton(btns, text="Open sign-in", width=120, command=self.ok).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda _: self.ok())
        self.bind("<Escape>", lambda _: self.destroy())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(100, self._grab)
        self.wait_window()

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def ok(self):
        self.profile_id = self.choices.get(self.menu.get())
        self.confirmed = True
        self.destroy()


# --------------------------------------------------------------------------- #
class MainWindow:
    """The window's contents exist only while it's open: closing it to the tray frees them, and
    starting hidden (at Windows login) never builds them. Syncing doesn't depend on the window."""

    POLL_VISIBLE_MS = 150   # react quickly while you're looking at the window
    POLL_HIDDEN_MS = 1000   # in the tray: only "show window" / "quit" / errors need handling

    def __init__(self, root: ctk.CTk, app, build: bool = True):
        self.root = root
        self.app = app
        self.hidden_notice_shown = False
        self.built = False

        root.title("Drive Sync")
        root.geometry("820x560")
        root.minsize(680, 460)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        app.apply_window_icon(root)
        if build:
            self.build()

    def build(self) -> None:
        root, app = self.root, self.app
        # ---- header: status + main actions ----
        hdr = ctk.CTkFrame(root, fg_color="transparent")
        hdr.pack(fill="x", padx=22, pady=(18, 6))
        hdr.columnconfigure(1, weight=1)
        self.dot = ctk.CTkLabel(hdr, text="●", font=font(22), width=24)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self.status_lbl = ctk.CTkLabel(hdr, text="", font=font(18, "bold"), anchor="w")
        self.status_lbl.grid(row=0, column=1, sticky="w")
        self.detail_lbl = ctk.CTkLabel(hdr, text="", font=font(12), text_color=MUTED, anchor="w", justify="left")
        # Full width under the buttons, wrapping to the window's width (long file names, speed, time left).
        self.detail_lbl.grid(row=1, column=1, columnspan=3, sticky="w", pady=(2, 0))
        autowrap(self.detail_lbl, hdr, margin=48)
        self.pause_btn = ctk.CTkButton(hdr, text="Pause", width=96, command=app.toggle_pause, **SECONDARY)
        self.pause_btn.grid(row=0, column=2, padx=(8, 0))
        self.sync_btn = ctk.CTkButton(hdr, text="Sync now", width=110, command=app.sync_now,
                                      font=font(13, "bold"))
        self.sync_btn.grid(row=0, column=3, padx=(8, 0))
        self.bar = ctk.CTkProgressBar(hdr, height=6, corner_radius=3)
        self.bar.set(0)

        tabs = ctk.CTkTabview(root, anchor="nw")
        tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._build_folders(tabs.add("Folders"))
        self._build_settings(tabs.add("Settings"))
        self._build_activity(tabs.add("Activity"))
        self.tabs = tabs
        self.built = True

        self.refresh_folders()
        self.refresh_account()
        self.refresh_state()

    def teardown(self) -> None:
        """Destroy the window's contents (they're rebuilt the next time it's opened)."""
        if not self.built:
            return
        self.built = False
        for w in self.root.winfo_children():
            w.destroy()
        for attr in ("tabs", "activity_view", "folder_list", "accounts_box", "dot", "status_lbl",
                     "detail_lbl", "pause_btn", "sync_btn", "bar"):
            self.__dict__.pop(attr, None)
        import gc
        gc.collect()

    # ------------------------------------------------------------------ tabs
    def _build_folders(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 8))
        ctk.CTkLabel(top, text="Folders backed up to Google Drive", font=font(14, "bold")).pack(side="left")
        ctk.CTkButton(top, text="+  Add folder", width=120, command=self.add_folder).pack(side="right")
        ctk.CTkButton(top, text="Open Google Drive", width=140, command=lambda: self.open_drive(None),
                      **SECONDARY).pack(side="right", padx=(0, 8))
        self.folder_list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.folder_list.pack(fill="both", expand=True)

    def _build_settings(self, tab):
        wrap = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        def card(title):
            c = ctk.CTkFrame(wrap, fg_color=CARD, corner_radius=10)
            c.pack(fill="x", padx=4, pady=(4, 10))
            ctk.CTkLabel(c, text=title, font=font(14, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
            return c

        acct = card("Google accounts")
        self.account_lbl = ctk.CTkLabel(acct, text="", font=font(12), text_color=MUTED, justify="left",
                                        anchor="w", wraplength=620)
        self.account_lbl.pack(anchor="w", padx=16)
        self.accounts_box = ctk.CTkFrame(acct, fg_color="transparent")
        self.accounts_box.pack(fill="x", padx=12, pady=(6, 0))
        row = ctk.CTkFrame(acct, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=(8, 14))
        self.add_account_btn = ctk.CTkButton(row, text="+  Add Google account", width=170,
                                             command=self.add_account)
        self.add_account_btn.pack(side="left")
        self.import_btn = ctk.CTkButton(row, text="Import credentials.json…", width=180,
                                        command=self.import_credentials, **SECONDARY)
        self.import_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Setup guide", width=100, command=self.app.open_readme, **SECONDARY).pack(
            side="left", padx=(8, 0))

        sync = card("Syncing")
        cfg = self.app.cfg
        self.watch_var = tk.BooleanVar(value=bool(cfg.get("watch_changes", True)))
        ctk.CTkSwitch(sync, text="Sync as soon as files change", variable=self.watch_var,
                      command=self.save_options, font=font()).pack(anchor="w", padx=16, pady=(2, 0))
        ctk.CTkLabel(sync, text="New, changed, renamed and deleted files are synced a few seconds after you "
                                "save them.", font=font(12), text_color=MUTED, anchor="w").pack(
            anchor="w", padx=60, pady=(0, 10))
        pr = ctk.CTkFrame(sync, fg_color="transparent")
        pr.pack(anchor="w", padx=16, pady=(0, 2))
        ctk.CTkLabel(pr, text="Check Google Drive for changes", font=font()).pack(side="left", padx=(0, 10))
        poll_now = next((k for k, v in POLLS.items() if v == cfg.get("drive_poll_seconds", 10)), "Every 10 seconds")
        self.poll_menu = ctk.CTkOptionMenu(pr, values=list(POLLS), width=170, command=lambda _: self.save_options())
        self.poll_menu.set(poll_now)
        self.poll_menu.pack(side="left")
        ctk.CTkLabel(sync, text="Two-way folders: how quickly changes made on Drive reach this PC. Each check is "
                                "one tiny request.", font=font(12), text_color=MUTED, anchor="w").pack(
            anchor="w", padx=16, pady=(0, 10))
        r = ctk.CTkFrame(sync, fg_color="transparent")
        r.pack(anchor="w", padx=16, pady=(2, 0))
        ctk.CTkLabel(r, text="Look for missed changes", font=font()).pack(side="left", padx=(0, 10))
        current = next((k for k, v in INTERVALS.items() if v == cfg.get("interval_minutes")), None)
        values = list(INTERVALS) if current else [f"Every {cfg.get('interval_minutes')} minutes", *INTERVALS]
        self.interval_menu = ctk.CTkOptionMenu(r, values=values, width=170, command=lambda _: self.save_options())
        self.interval_menu.set(current or values[0])
        self.interval_menu.pack(side="left")
        ctk.CTkLabel(sync, text="Two-way folders compare file sizes and times on this PC (a few seconds, no "
                                "internet) and check every file on both sides once a day. Sync now checks "
                                "every file right away.", font=font(12), text_color=MUTED, anchor="w",
                     justify="left", wraplength=560).pack(anchor="w", padx=16, pady=(4, 0))
        self.notify_var = tk.BooleanVar(value=bool(cfg.get("notify_on_changes")))
        ctk.CTkSwitch(sync, text="Show a notification when files are uploaded", variable=self.notify_var,
                      command=self.save_options, font=font()).pack(anchor="w", padx=16, pady=(12, 0))
        self.startup_var = tk.BooleanVar(value=self.app.startup_enabled())
        ctk.CTkSwitch(sync, text="Start Drive Sync when I sign in to Windows", variable=self.startup_var,
                      command=self.toggle_startup, font=font()).pack(anchor="w", padx=16, pady=(10, 14))

        net = card("Internet use")
        r = ctk.CTkFrame(net, fg_color="transparent")
        r.pack(anchor="w", padx=16, pady=(2, 0))
        ctk.CTkLabel(r, text="Priority", font=font()).pack(side="left", padx=(0, 10))
        self.priority_btn = ctk.CTkSegmentedButton(r, values=["Normal", "High"], command=lambda _: self.save_options())
        self.priority_btn.set("High" if cfg.get("high_priority") else "Normal")
        self.priority_btn.pack(side="left")
        prio_help = ctk.CTkLabel(net, text="High: big downloads (16 MB+) use 4 connections at once, so Drive Sync gets "
                                           "more of your internet while other apps are using it. It can't make the "
                                           "line itself faster, and it doesn't take priority over other devices on "
                                           "your network (your router decides that).",
                                 font=font(12), text_color=MUTED, anchor="w", justify="left")
        prio_help.pack(anchor="w", padx=16, pady=(4, 10), fill="x")
        autowrap(prio_help, net, margin=40)
        r = ctk.CTkFrame(net, fg_color="transparent")
        r.pack(anchor="w", padx=16, pady=(2, 0))
        ctk.CTkLabel(r, text="Speed limit", font=font()).pack(side="left", padx=(0, 10))
        limit_now = next((k for k, v in LIMITS.items() if v == cfg.get("speed_limit_kbps", 0)),
                         f"{cfg.get('speed_limit_kbps')} KB/s")
        self.limit_menu = ctk.CTkOptionMenu(r, values=list(LIMITS), width=170, command=lambda _: self.save_options())
        self.limit_menu.set(limit_now)
        self.limit_menu.pack(side="left")
        limit_help = ctk.CTkLabel(net, text="Caps Drive Sync's total upload + download speed, to keep the rest of "
                                            "your internet free (e.g. for video calls). Applies immediately.",
                                  font=font(12), text_color=MUTED, anchor="w", justify="left")
        limit_help.pack(anchor="w", padx=16, pady=(4, 10), fill="x")
        autowrap(limit_help, net, margin=40)
        self.metered_var = tk.BooleanVar(value=bool(cfg.get("pause_on_metered")))
        ctk.CTkSwitch(net, text="Don't sync on metered connections", variable=self.metered_var,
                      command=self.save_options, font=font()).pack(anchor="w", padx=16, pady=(2, 0))
        metered_help = ctk.CTkLabel(net, text="Waits while you're on a phone hotspot, mobile data or Wi-Fi set as "
                                              "“metered” in Windows Settings, and carries on by itself on another "
                                              "network. Stops at the next file if the connection changes mid-sync.",
                                    font=font(12), text_color=MUTED, anchor="w", justify="left")
        metered_help.pack(anchor="w", padx=16, pady=(4, 14), fill="x")
        autowrap(metered_help, net, margin=40)

        look = card("Appearance")
        self.theme_btn = ctk.CTkSegmentedButton(look, values=["System", "Light", "Dark"],
                                                command=self.set_appearance)
        self.theme_btn.set(cfg.get("appearance", "System"))
        self.theme_btn.pack(anchor="w", padx=16, pady=(2, 14))

        bottom = ctk.CTkFrame(wrap, fg_color="transparent")
        bottom.pack(fill="x", padx=4, pady=(6, 4))
        ctk.CTkLabel(bottom, text="Closing this window keeps Drive Sync running in the system tray.",
                     font=font(12), text_color=MUTED).pack(side="left")
        ctk.CTkButton(bottom, text="Quit Drive Sync", width=130, command=self.confirm_quit, **DANGER).pack(
            side="right")

    def _build_activity(self, tab):
        self.activity_view = ActivityView(tab, self.app)

    # ------------------------------------------------------------ refreshers
    def refresh_state(self):
        app = self.app
        color = STATE_COLORS["paused" if app.paused else app.state]
        self.dot.configure(text_color=color)
        self.status_lbl.configure(text=app.status)
        files = app.file_lines if app.state == "syncing" else []
        self.detail_lbl.configure(text="\n".join([app.detail, *files]))  # one line per file at once
        self.pause_btn.configure(text="Resume" if app.paused else "Pause")
        self.sync_btn.configure(state="disabled" if app.state == "syncing" else "normal")
        prog = app.progress
        if app.state == "syncing" and prog is not None and prog.total:
            self.bar.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(6, 0))
            self.bar.set(prog.percent / 100)
        else:
            self.bar.grid_remove()

    def refresh_folders(self):
        for w in self.folder_list.winfo_children():
            w.destroy()
        folders = self.app.cfg.get("folders", [])
        if not folders:
            box = ctk.CTkFrame(self.folder_list, fg_color=CARD, corner_radius=10)
            box.pack(fill="x", padx=4, pady=30)
            ctk.CTkLabel(box, text="No folders yet", font=font(15, "bold")).pack(pady=(22, 2))
            ctk.CTkLabel(box, text="Choose a folder on this PC and Drive Sync will keep a copy in Google Drive.",
                         font=font(), text_color=MUTED).pack()
            ctk.CTkButton(box, text="+  Add folder", width=130, command=self.add_folder).pack(pady=(14, 22))
            return
        info = self.app.accounts_info()
        known = [a["email"] for a in info]
        signin = {a["email"] for a in info if a["needs_signin"]}
        for i, f in enumerate(folders):
            c = ctk.CTkFrame(self.folder_list, fg_color=CARD, corner_radius=10)
            c.pack(fill="x", padx=4, pady=(0, 8))
            c.columnconfigure(0, weight=1)
            title = ctk.CTkLabel(c, text=f.get("local", ""), font=font(13, "bold"), anchor="w", justify="left")
            title.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
            if f.get("drive_folder_id"):   # chosen in the Drive browser: already a full path
                target = f.get("drive_folder") or f"folder id {f['drive_folder_id']}"
            else:                          # typed path: always inside My Drive
                target = f"My Drive/{_strip_my_drive(f.get('drive_folder', ''))}"
            if f.get("mode", "two_way") == "two_way":
                extra = " · two-way"
            else:
                extra = " · upload only" + (", Drive matches PC" if f.get("delete") else "")
            if f.get("exclude"):
                extra += f" · skips {', '.join(f['exclude'])}"
            exists = os.path.isdir(f.get("local", ""))
            email = self.app.folder_account(f, known)
            if not exists:
                line, color = "⚠  This folder no longer exists on this PC", "#d93025"
            elif not email:
                line, color = "⚠  No Google account - add one in Settings", "#d93025"
            elif email not in known:
                line, color = f"⚠  Account {email} was removed - click Edit to choose another", "#d93025"
            elif email in signin:
                line, color = f"⚠  {email} needs to sign in again (Settings → Google accounts)", "#d93025"
            else:
                arrow = "↔" if f.get("mode", "two_way") == "two_way" else "→"  # (⇄ isn't in Segoe UI)
                line, color = f"{arrow}  {email} · {target}{extra}", MUTED
            info = ctk.CTkLabel(c, text=line, font=font(12), text_color=color, anchor="w", justify="left")
            # Full card width under the buttons, wrapping to the window's width.
            info.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(4, 12))
            btns = ctk.CTkFrame(c, fg_color="transparent")
            btns.grid(row=0, column=1, padx=12, pady=(10, 0))
            autowrap(info, c, margin=40)
            autowrap(title, c, margin=lambda b=btns: b.winfo_width() + 60)  # beside the buttons
            for text, cmd, style in (("Open", lambda i=i: self.open_local(i), SECONDARY),
                                     ("Drive", lambda i=i: self.open_drive(i), SECONDARY),
                                     ("Edit", lambda i=i: self.edit_folder(i), SECONDARY),
                                     ("Remove", lambda i=i: self.remove_folder(i), DANGER)):
                ctk.CTkButton(btns, text=text, width=64, height=28, command=cmd, **style).pack(
                    side="left", padx=(6, 0))

    def refresh_account(self):
        """Rebuild the Google accounts list in Settings (and folder cards, which show accounts)."""
        ready = self.app.credentials_ready()
        info = self.app.accounts_info()
        busy = self.app.signing_in
        if not ready:
            text = ("First, import the OAuth client file (credentials.json) from Google Cloud Console. "
                    "The setup guide explains each step.")
        elif busy:
            text = ("Waiting for you in the browser… choose the Google account and allow access. "
                    "(This window updates by itself when you're done.)")
        elif not info:
            text = ("Add the Google account to back up to. Your browser opens so you can choose which "
                    "account - you can add more than one and pick one per folder.")
        else:
            text = ("Each folder syncs to the account chosen for it (Folders → Edit). "
                    "Drive Sync can only see files it created.")
        self.account_lbl.configure(text=text)
        self.add_account_btn.configure(state="normal" if ready and not busy else "disabled",
                                       text="Waiting for browser…" if busy else "+  Add Google account")

        for w in self.accounts_box.winfo_children():
            w.destroy()
        profile_labels = self.profile_choices() if info else {}
        for a in info:
            r = ctk.CTkFrame(self.accounts_box, fg_color=("#ffffff", "#333333"), corner_radius=8)
            r.pack(fill="x", padx=4, pady=(0, 6))
            r.columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text="●", font=font(16), width=20,
                         text_color="#d93025" if a["needs_signin"] else "#1e8e3e").grid(
                row=0, column=0, rowspan=2, padx=(10, 6), pady=8)
            ctk.CTkLabel(r, text=a["email"], font=font(13, "bold"), anchor="w").grid(
                row=0, column=1, sticky="w", pady=(8, 0))
            n = a["folders"]
            status = "Click “Sign in again” to keep syncing (needs updated access)" if a["needs_signin"] else (
                f"{n} folder{'s' if n != 1 else ''}" if n else "No folders yet")
            if a.get("storage") and not a["needs_signin"]:
                status += f"  ·  Drive: {a['storage']}" + ("  -  nearly full" if a.get("nearly_full") else "")
            ctk.CTkLabel(r, text=status, font=font(12), anchor="w",
                         text_color="#d93025" if a["needs_signin"] or a.get("nearly_full") else MUTED).grid(
                row=1, column=1, sticky="w", pady=(0, 8))
            pr = ctk.CTkFrame(r, fg_color="transparent")
            pr.grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 10))
            ctk.CTkLabel(pr, text="Open Drive links and sign-in in:", font=font(12), text_color=MUTED).pack(
                side="left", padx=(0, 8))
            labels = list(profile_labels)
            current = self._label_for(self.app.account_profile(a["email"]), profile_labels)
            if current not in labels:
                labels.append(current)
            menu = ctk.CTkOptionMenu(pr, values=labels, width=240, height=26, dynamic_resizing=False,
                                     command=lambda label, e=a["email"]: self.app.set_account_profile(
                                         e, profile_labels.get(label)))
            menu.set(current)
            menu.pack(side="left")
            b = ctk.CTkFrame(r, fg_color="transparent")
            b.grid(row=0, column=2, rowspan=2, padx=8)
            if a["needs_signin"]:
                ctk.CTkButton(b, text="Sign in again", width=110, height=28,
                              state="normal" if ready and not busy else "disabled",
                              command=lambda e=a["email"]: self.app.add_account(e)).pack(side="left", padx=(6, 0))
            ctk.CTkButton(b, text="Remove", width=80, height=28, **DANGER,
                          command=lambda e=a["email"], n=n: self.remove_account(e, n)).pack(side="left", padx=(6, 0))
        self.refresh_folders()


    # --------------------------------------------------------------- actions
    def add_folder(self):
        d = FolderDialog(self.root, self.app, "Add folder")
        if d.result:
            cfg = dict(self.app.cfg)
            known = [a["email"] for a in self.app.accounts_info()]

            def key(f):
                return os.path.normcase(f["local"]), self.app.folder_account(f, known)

            if any(key(f) == key(d.result) for f in cfg["folders"]):
                messagebox.showinfo("Drive Sync", "That folder is already being synced to this account.",
                                    parent=self.root)
                return
            cfg["folders"] = [*cfg["folders"], d.result]
            self.app.save_config(cfg)
            self.refresh_folders()
            self.app.sync_now()

    def edit_folder(self, i: int):
        cfg = dict(self.app.cfg)
        d = FolderDialog(self.root, self.app, "Edit folder", cfg["folders"][i])
        if d.result:
            cfg["folders"] = list(cfg["folders"])
            cfg["folders"][i] = d.result
            self.app.save_config(cfg)
            self.refresh_folders()

    def remove_folder(self, i: int):
        cfg = dict(self.app.cfg)
        f = cfg["folders"][i]
        if messagebox.askyesno("Drive Sync", f"Stop syncing this folder?\n\n{f['local']}\n\n"
                               "Files already on Google Drive are not deleted.", parent=self.root):
            cfg["folders"] = [x for j, x in enumerate(cfg["folders"]) if j != i]
            self.app.save_config(cfg)
            self.refresh_folders()

    def open_local(self, i: int):
        path = self.app.cfg["folders"][i]["local"]
        if os.path.isdir(path):
            os.startfile(path)  # opens the user's own folder in Explorer

    def open_drive(self, i: int | None):
        rid, email = None, None
        known = [a["email"] for a in self.app.accounts_info()]
        if i is not None:
            f = self.app.cfg["folders"][i]
            email = self.app.folder_account(f, known)
            rid = f.get("drive_folder_id") or self.app.remote_ids.get((os.path.normcase(f["local"]), email))
        elif len(known) == 1:
            email = known[0]
        url = f"https://drive.google.com/drive/folders/{rid}" if rid else "https://drive.google.com/drive/my-drive"
        if email:
            url += "?" + urlencode({"authuser": email})  # the right account inside the profile
        self.app.open_url(url, email)                     # the right browser profile

    # ------------------------------------------------------- browser profiles
    DEFAULT_BROWSER = "Default browser"

    def profile_choices(self) -> dict[str, str | None]:
        """{label shown in menus: profile id}; the first entry is the default browser."""
        choices: dict[str, str | None] = {self.DEFAULT_BROWSER: None}
        for p in browsers.list_profiles():
            label = p.label if p.label not in choices else f"{p.label} ({p.directory})"
            choices[label] = p.id
        return choices

    def _label_for(self, profile_id: str | None, choices: dict[str, str | None]) -> str:
        if not profile_id:
            return self.DEFAULT_BROWSER
        return next((label for label, pid in choices.items() if pid == profile_id),
                    f"Missing profile ({profile_id.split('|')[-1]})")

    def save_options(self):
        choice = self.interval_menu.get()
        minutes = INTERVALS.get(choice, self.app.cfg.get("interval_minutes", 15))
        cfg = dict(self.app.cfg, interval_minutes=minutes, notify_on_changes=self.notify_var.get(),
                   watch_changes=self.watch_var.get(),
                   drive_poll_seconds=POLLS.get(self.poll_menu.get(), 10),
                   high_priority=self.priority_btn.get() == "High",
                   pause_on_metered=self.metered_var.get(),
                   speed_limit_kbps=LIMITS.get(self.limit_menu.get(), self.app.cfg.get("speed_limit_kbps", 0)))
        if cfg != self.app.cfg:
            self.app.save_config(cfg)

    def set_appearance(self, value: str):
        ctk.set_appearance_mode(value.lower())
        self.app.save_config(dict(self.app.cfg, appearance=value))

    def toggle_startup(self):
        try:
            self.app.set_startup(self.startup_var.get())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Drive Sync", f"Couldn't change the startup setting:\n{e}", parent=self.root)
        self.startup_var.set(self.app.startup_enabled())

    def import_credentials(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose the credentials.json you downloaded",
                                          filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                                          initialdir=os.path.expanduser("~/Downloads"))
        if not path:
            return
        try:
            self.app.import_credentials(Path(path))
        except ValueError as e:
            messagebox.showerror("Drive Sync", str(e), parent=self.root)
            return
        self.refresh_account()
        if not self.app.accounts_info():
            self.add_account()

    def add_account(self):
        d = AddAccountDialog(self.root, self.app, self.profile_choices())
        if d.confirmed:
            self.app.add_account(browser_profile=d.profile_id)

    def remove_account(self, email: str, n_folders: int):
        warn = (f"\n\n{n_folders} folder{'s' if n_folders != 1 else ''} use this account and will stop "
                "syncing until you choose another account for them.") if n_folders else ""
        if messagebox.askyesno("Drive Sync", f"Remove {email} from Drive Sync?\n\nDrive Sync's access to this "
                               f"account is revoked. Files already on Google Drive are not deleted.{warn}",
                               parent=self.root):
            self.app.set_account_profile(email, None)
            self.app.remove_account(email)

    def on_account_added(self, email: str, browser_profile: str | None = None):
        """Folders saved without an account (older versions / before any account existed) get this one."""
        if browser_profile:
            self.app.set_account_profile(email, browser_profile)
        cfg = dict(self.app.cfg)
        changed = False
        folders = []
        for f in cfg.get("folders", []):
            if not f.get("account"):
                f = {**f, "account": email}
                changed = True
            folders.append(f)
        if changed:
            self.app.save_config({**cfg, "folders": folders})
        if self.built:
            self.refresh_account()
        self.app.notify(f"Google account added: {email}")

    def confirm_quit(self):
        if messagebox.askyesno("Drive Sync", "Quit Drive Sync? Folders won't sync until it starts again.",
                               parent=self.root):
            self.app.quit()

    # ------------------------------------------------------------ show/hide
    def show(self):
        if not self.built:
            self.build()
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        if self.root.grab_current() is not None:
            return  # a dialog (Add folder, Choose folder...) is open - finish or cancel it first
        self.root.withdraw()
        self.teardown()
        if not self.hidden_notice_shown:
            self.hidden_notice_shown = True
            self.app.notify("Drive Sync is still running in the system tray.")

    # ------------------------------------------------------------- polling
    def poll(self):
        """Handle messages from the worker / tray threads (tkinter isn't thread-safe)."""
        try:
            while True:
                msg = self.app.ui.get_nowait()
                kind = msg[0]
                if kind in ("state", "activity", "last_check", "account") and not self.built:
                    continue  # nothing on screen to update; it's all re-read when the window opens
                if kind == "state":
                    self.refresh_state()
                elif kind == "activity":
                    self.activity_view.add(msg[1])
                elif kind == "last_check":
                    self.activity_view.refresh_header()
                elif kind == "account":
                    self.refresh_account()
                elif kind == "account_added":
                    self.on_account_added(*msg[1:])
                elif kind == "error":
                    self.show()
                    messagebox.showerror("Drive Sync", msg[1], parent=self.root)
                elif kind == "show":
                    self.show()
                elif kind == "quit":
                    self.app.quit()
                    return
        except queue.Empty:
            pass
        if self.app.show_flag.exists():
            try:
                self.app.show_flag.unlink()
            except OSError:
                pass
            self.show()
        self.root.after(self.POLL_VISIBLE_MS if self.built else self.POLL_HIDDEN_MS, self.poll)
